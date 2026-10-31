# language: Python, file: bot.py, target: personal Telegram deep-parser bot
# *aiogram 3 + FSM keeps state clean; one active parser per user is enough for personal use.*

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

import config as cfg
from parser_core import DeepParser, ParserStats, ProxyPool

# ── bot setup ─────────────────────────────────────────────────────────────
bot = Bot(token=cfg.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# active parsers: user_id → DeepParser
ACTIVE: dict[int, DeepParser] = {}
STATUS_MSG: dict[int, int] = {}          # user_id → message_id of live status


class Form(StatesGroup):
    waiting_dorks = State()
    waiting_proxies = State()


# ── helpers ───────────────────────────────────────────────────────────────

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📂 Upload Dorks", callback_data="upload_dorks")],
        [InlineKeyboardButton(text="🌐 Upload Proxies (optional)", callback_data="upload_proxies")],
        [InlineKeyboardButton(text="⚡ Start Deep Parser", callback_data="start_parser")],
        [
            InlineKeyboardButton(text="⏸ Pause", callback_data="pause"),
            InlineKeyboardButton(text="▶ Resume", callback_data="resume"),
            InlineKeyboardButton(text="⏹ Stop", callback_data="stop"),
        ],
        [InlineKeyboardButton(text="📥 Download Results", callback_data="download")],
        [InlineKeyboardButton(text="📊 Status", callback_data="status")],
        [InlineKeyboardButton(text="🗑 Clear Session", callback_data="clear")],
    ])


def stats_text(st: ParserStats) -> str:
    d = st.to_dict()
    bar = "█" * min(20, int(d["dorks_used"] / max(d["dorks_total"], 1) * 20))
    bar += "░" * (20 - len(bar))
    return (
        f"<b>⚡ VENOM DEEP PARSER — Yahoo JP</b>\n"
        f"<code>{bar}</code> {d['dorks_used']}/{d['dorks_total']}\n\n"
        f"🔗 Raw links     <b>{d['raw']}</b>\n"
        f"✨ Unique       <b>{d['unique']}</b>\n"
        f"✅ Live urls    <b>{d['live']}</b>\n"
        f"🗑 Filtered     <b>{d['filtered']}</b>\n"
        f"📦 Pool left    <b>{d['pool_left']}</b>\n"
        f"⚠ Errors       <b>{d['errors']}</b>\n"
        f"🚀 Speed        <b>~{d['speed']} url/s</b>"
    )


async def allowed(user_id: int) -> bool:
    if not cfg.ALLOWED_USER_IDS:
        return True
    return user_id in cfg.ALLOWED_USER_IDS


# ── handlers ──────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    if not await allowed(message.from_user.id):
        return await message.answer("Access denied.")
    await state.clear()
    await message.answer(
        "<b>⚡ VENOM DEEP PARSER</b>\n"
        "Yahoo Japan · personal mode · no license\n\n"
        "Upload a dork list → Start → watch live results.\n"
        "Ready when you are.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "upload_dorks")
async def cb_upload_dorks(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(Form.waiting_dorks)
    await cq.message.answer("Send the dork file (.txt or .csv) or paste dorks as text.")


@router.callback_query(F.data == "upload_proxies")
async def cb_upload_proxies(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    await state.set_state(Form.waiting_proxies)
    await cq.message.answer("Send proxies file (one per line: http://user:pass@host:port) or paste them.")


@router.message(Form.waiting_dorks)
async def on_dorks(message: Message, state: FSMContext):
    uid = message.from_user.id
    dorks: list[str] = []

    if message.document:
        file = await bot.get_file(message.document.file_id)
        content = await bot.download_file(file.file_path)
        text = content.read().decode("utf-8", errors="ignore")
    else:
        text = message.text or ""

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # csv: take first column
        if "," in line:
            line = line.split(",")[0].strip().strip('"')
        dorks.append(line)

    if not dorks:
        return await message.answer("No dorks found. Try again.")

    path = cfg.DATA_DIR / f"dorks_{uid}.txt"
    path.write_text("\n".join(dorks), encoding="utf-8")
    await state.update_data(dorks_path=str(path), dork_count=len(dorks))
    await state.set_state(None)
    await message.answer(
        f"✅ Loaded <b>{len(dorks)}</b> dorks.\nPress <b>Start Deep Parser</b> when ready.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.message(Form.waiting_proxies)
async def on_proxies(message: Message, state: FSMContext):
    uid = message.from_user.id
    lines: list[str] = []

    if message.document:
        file = await bot.get_file(message.document.file_id)
        content = await bot.download_file(file.file_path)
        text = content.read().decode("utf-8", errors="ignore")
    else:
        text = message.text or ""

    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)

    path = cfg.DATA_DIR / f"proxies_{uid}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    await state.update_data(proxy_path=str(path), proxy_count=len(lines))
    await state.set_state(None)
    await message.answer(
        f"✅ Loaded <b>{len(lines)}</b> proxies.",
        reply_markup=main_menu(),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "start_parser")
async def cb_start(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    uid = cq.from_user.id
    data = await state.get_data()

    if uid in ACTIVE and not ACTIVE[uid]._stop.is_set():
        return await cq.message.answer("Parser already running. Stop it first.")

    dorks_path = data.get("dorks_path")
    if not dorks_path or not Path(dorks_path).exists():
        return await cq.message.answer("No dorks loaded. Upload a list first.")

    dorks = Path(dorks_path).read_text(encoding="utf-8").splitlines()
    proxy_path = data.get("proxy_path")
    pool = ProxyPool.from_file(Path(proxy_path)) if proxy_path else ProxyPool.from_file(cfg.PROXY_FILE)

    session_id = f"u{uid}_{int(time.time())}"

    async def on_progress(st: ParserStats):
        text = stats_text(st)
        mid = STATUS_MSG.get(uid)
        try:
            if mid:
                await bot.edit_message_text(
                    text, chat_id=cq.message.chat.id, message_id=mid, parse_mode="HTML"
                )
            else:
                msg = await bot.send_message(cq.message.chat.id, text, parse_mode="HTML")
                STATUS_MSG[uid] = msg.message_id
        except Exception:
            pass

    parser = DeepParser(
        dorks=dorks,
        proxy_pool=pool,
        on_progress=on_progress,
        session_id=session_id,
    )
    ACTIVE[uid] = parser

    msg = await cq.message.answer(stats_text(parser.stats), parse_mode="HTML")
    STATUS_MSG[uid] = msg.message_id

    async def runner():
        try:
            await parser.run(resume=True)
            await bot.send_message(
                cq.message.chat.id,
                f"✅ Finished.\nLive urls: <b>{parser.stats.live}</b>\nUse Download Results.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception as e:
            logger.exception(e)
            await bot.send_message(cq.message.chat.id, f"Parser error: {e}")
        finally:
            ACTIVE.pop(uid, None)

    asyncio.create_task(runner())


@router.callback_query(F.data == "pause")
async def cb_pause(cq: CallbackQuery):
    await cq.answer()
    p = ACTIVE.get(cq.from_user.id)
    if p:
        p.pause()
        await cq.message.answer("⏸ Paused.")
    else:
        await cq.message.answer("No active parser.")


@router.callback_query(F.data == "resume")
async def cb_resume(cq: CallbackQuery):
    await cq.answer()
    p = ACTIVE.get(cq.from_user.id)
    if p:
        p.resume()
        await cq.message.answer("▶ Resumed.")
    else:
        await cq.message.answer("No active parser.")


@router.callback_query(F.data == "stop")
async def cb_stop(cq: CallbackQuery):
    await cq.answer()
    p = ACTIVE.get(cq.from_user.id)
    if p:
        p.stop()
        await cq.message.answer("⏹ Stop signal sent. Finishing current wave…")
    else:
        await cq.message.answer("No active parser.")


@router.callback_query(F.data == "status")
async def cb_status(cq: CallbackQuery):
    await cq.answer()
    p = ACTIVE.get(cq.from_user.id)
    if p:
        await cq.message.answer(stats_text(p.stats), parse_mode="HTML")
    else:
        await cq.message.answer("No active parser.")


@router.callback_query(F.data == "download")
async def cb_download(cq: CallbackQuery):
    await cq.answer()
    uid = cq.from_user.id
    p = ACTIVE.get(uid)

    # also look for last result files
    files = sorted(cfg.RESULTS_DIR.glob(f"live_u{uid}_*.txt"), reverse=True)
    if p and p._live:
        path = p.export("txt")
        await cq.message.answer_document(
            FSInputFile(path),
            caption=f"Live urls: {len(p._live)}",
        )
        # also json
        path_j = p.export("json")
        await cq.message.answer_document(FSInputFile(path_j))
    elif files:
        path = files[0]
        await cq.message.answer_document(FSInputFile(path), caption="Last saved results")
    else:
        await cq.message.answer("No results yet.")


@router.callback_query(F.data == "clear")
async def cb_clear(cq: CallbackQuery, state: FSMContext):
    await cq.answer()
    uid = cq.from_user.id
    if uid in ACTIVE:
        ACTIVE[uid].stop()
        ACTIVE.pop(uid, None)
    STATUS_MSG.pop(uid, None)
    await state.clear()
    # optional: wipe user dork/proxy files
    for f in cfg.DATA_DIR.glob(f"*_{uid}.*"):
        f.unlink(missing_ok=True)
    await cq.message.answer("Session cleared.", reply_markup=main_menu())


# ── entry ─────────────────────────────────────────────────────────────────

async def main():
    logger.info("Venom Deep Parser bot starting…")
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
