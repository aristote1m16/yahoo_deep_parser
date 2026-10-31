# language: Python, file: run_cli.py, target: Kaggle / headless deep parser
# *Same engine as the bot. Use this on 2×T4 when you only need the harvest, no Telegram.*

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from loguru import logger

from parser_core import DeepParser, ProxyPool, ParserStats
import config as cfg


async def progress(st: ParserStats):
    d = st.to_dict()
    logger.info(
        f"[{d['dorks_used']}/{d['dorks_total']}] "
        f"raw={d['raw']} unique={d['unique']} live={d['live']} "
        f"filtered={d['filtered']} err={d['errors']} ~{d['speed']} url/s"
    )


async def main():
    ap = argparse.ArgumentParser(description="Yahoo JP Deep Parser — CLI / Kaggle")
    ap.add_argument("-d", "--dorks", required=True, help="path to dork list (txt)")
    ap.add_argument("-p", "--proxies", default=None, help="path to proxy list")
    ap.add_argument("-o", "--out", default="txt", choices=["txt", "csv", "json"])
    ap.add_argument("--session", default="cli")
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()

    dorks = Path(args.dorks).read_text(encoding="utf-8").splitlines()
    pool = ProxyPool.from_file(Path(args.proxies)) if args.proxies else ProxyPool.from_file(cfg.PROXY_FILE)

    parser = DeepParser(
        dorks=dorks,
        proxy_pool=pool,
        on_progress=progress,
        session_id=args.session,
    )
    live = await parser.run(resume=not args.no_resume)
    path = parser.export(args.out)
    logger.success(f"Done. {len(live)} live urls → {path}")


if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    asyncio.run(main())
