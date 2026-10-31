# language: Python, file: parser_core.py, target: Yahoo Japan deep parser, asyncio + high concurrency
# *httpx with HTTP/2 + connection limits is the real speed lever; more workers than proxies just burns 429s.*

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
import orjson
from loguru import logger
from selectolax.parser import HTMLParser
from tenacity import retry, stop_after_attempt, wait_exponential

import config as cfg


# ── Data containers ───────────────────────────────────────────────────────

@dataclass
class SerpResult:
    title: str
    url: str
    snippet: str = ""
    dork: str = ""

@dataclass
class LiveUrl:
    url: str
    status: int
    title: str = ""
    dork: str = ""
    content_length: int = 0
    final_url: str = ""

@dataclass
class ParserStats:
    raw: int = 0
    unique: int = 0
    live: int = 0
    filtered: int = 0
    dorks_used: int = 0
    dorks_total: int = 0
    errors: int = 0
    start_ts: float = field(default_factory=time.time)
    last_speed: float = 0.0

    def speed(self) -> float:
        elapsed = max(time.time() - self.start_ts, 0.1)
        return self.raw / elapsed

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "unique": self.unique,
            "live": self.live,
            "filtered": self.filtered,
            "dorks_used": self.dorks_used,
            "dorks_total": self.dorks_total,
            "errors": self.errors,
            "speed": round(self.speed(), 1),
            "pool_left": max(self.dorks_total - self.dorks_used, 0),
        }


# ── Proxy pool ────────────────────────────────────────────────────────────

class ProxyPool:
    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or []
        self._failures: dict[str, int] = {}
        self._idx = 0
        self._lock = asyncio.Lock()

    @classmethod
    def from_file(cls, path: Path) -> "ProxyPool":
        if not path.exists():
            return cls([])
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
        return cls(lines)

    def empty(self) -> bool:
        return len(self._proxies) == 0

    async def next(self) -> Optional[str]:
        if not self._proxies:
            return None
        async with self._lock:
            for _ in range(len(self._proxies)):
                p = self._proxies[self._idx % len(self._proxies)]
                self._idx += 1
                if self._failures.get(p, 0) < cfg.MAX_PROXY_FAILURES:
                    return p
            return None

    async def report_fail(self, proxy: Optional[str]):
        if not proxy:
            return
        async with self._lock:
            self._failures[proxy] = self._failures.get(proxy, 0) + 1
            if self._failures[proxy] >= cfg.MAX_PROXY_FAILURES:
                logger.warning(f"proxy dead → {proxy}")

    async def report_ok(self, proxy: Optional[str]):
        if not proxy:
            return
        async with self._lock:
            self._failures[proxy] = 0


# ── User-Agent rotation ───────────────────────────────────────────────────

_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]

def _ua() -> str:
    import random
    return random.choice(_UAS) if cfg.USER_AGENT_ROTATE else _UAS[0]


# ── HTTP client factory ───────────────────────────────────────────────────

def make_client(proxy: Optional[str] = None) -> httpx.AsyncClient:
    limits = httpx.Limits(
        max_connections=cfg.MAX_CONCURRENT_REQUESTS,
        max_keepalive_connections=40,
        keepalive_expiry=30.0,
    )
    timeout = httpx.Timeout(
        connect=cfg.CONNECT_TIMEOUT,
        read=cfg.REQUEST_TIMEOUT,
        write=cfg.REQUEST_TIMEOUT,
        pool=cfg.REQUEST_TIMEOUT,
    )
    proxies = proxy if proxy else None
    return httpx.AsyncClient(
        http2=True,
        limits=limits,
        timeout=timeout,
        proxies=proxies,
        follow_redirects=True,
        headers={
            "User-Agent": _ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Cache-Control": "no-cache",
        },
    )


# ── SERP extraction ───────────────────────────────────────────────────────

def extract_yahoo_jp(html: str, dork: str) -> list[SerpResult]:
    """Resilient extraction for Yahoo Japan SERP. Multiple selector fallbacks."""
    tree = HTMLParser(html)
    results: list[SerpResult] = []

    # Primary: classic result blocks
    for node in tree.css("div.sw-CardBase, div.Algo, div.ss-card, li.w, div.Contents__itemitem"):
        a = node.css_first("a")
        if not a:
            continue
        href = a.attributes.get("href") or ""
        title = a.text(strip=True) or ""
        if not href.startswith("http"):
            continue
        # clean tracking redirects if present
        if "yahoo.co.jp" in href and "RU=" in href:
            m = re.search(r"RU=([^/&]+)", href)
            if m:
                from urllib.parse import unquote
                href = unquote(m.group(1))
        snip_node = node.css_first("div.sw-Card__summary, p.Algo__desc, span.s, div.Contents__desc")
        snippet = snip_node.text(strip=True) if snip_node else ""
        results.append(SerpResult(title=title, url=href, snippet=snippet, dork=dork))

    # Fallback: any external link that looks like a result
    if not results:
        for a in tree.css("a"):
            href = a.attributes.get("href") or ""
            if not href.startswith("http"):
                continue
            if any(x in href for x in ("yahoo.co.jp", "yahoo.com", "bing.com", "google.")):
                continue
            title = a.text(strip=True)
            if len(title) < 4:
                continue
            results.append(SerpResult(title=title, url=href, snippet="", dork=dork))

    return results


# ── Live check ────────────────────────────────────────────────────────────

async def live_check(client: httpx.AsyncClient, url: str, dork: str) -> Optional[LiveUrl]:
    try:
        r = await client.head(url, timeout=cfg.LIVE_CHECK_TIMEOUT)
        status = r.status_code
        if status in cfg.REJECT_STATUS:
            return None
        # some servers reject HEAD → fall back to GET range
        if status == 405 or status == 403:
            r = await client.get(url, headers={"Range": "bytes=0-1023"}, timeout=cfg.LIVE_CHECK_TIMEOUT)
            status = r.status_code
        cl = int(r.headers.get("content-length") or 0)
        if cl and cl < cfg.MIN_CONTENT_LENGTH:
            return None
        final = str(r.url)
        # soft-404 body check only on small responses
        title = ""
        if status < 400 and cl < 50_000:
            try:
                body = (await client.get(url, timeout=cfg.LIVE_CHECK_TIMEOUT)).text[:4000]
                if any(m.lower() in body.lower() for m in cfg.SOFT_404_MARKERS):
                    return None
                t = HTMLParser(body).css_first("title")
                title = t.text(strip=True) if t else ""
            except Exception:
                pass
        return LiveUrl(url=url, status=status, title=title, dork=dork, content_length=cl, final_url=final)
    except Exception:
        return None


# ── Main parser engine ────────────────────────────────────────────────────

class DeepParser:
    def __init__(
        self,
        dorks: list[str],
        proxy_pool: ProxyPool | None = None,
        on_progress=None,               # async callable(stats: ParserStats)
        session_id: str = "default",
    ):
        self.dorks = [d.strip() for d in dorks if d.strip()]
        self.proxy_pool = proxy_pool or ProxyPool()
        self.on_progress = on_progress
        self.session_id = session_id
        self.stats = ParserStats(dorks_total=len(self.dorks))
        self._seen: set[str] = set()
        self._live: list[LiveUrl] = []
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._pause = asyncio.Event()
        self._pause.set()               # not paused by default
        self._sem = asyncio.Semaphore(cfg.MAX_CONCURRENT_REQUESTS)

    # ── control ───────────────────────────────────────────────────────────
    def stop(self):
        self._stop.set()

    def pause(self):
        self._pause.clear()

    def resume(self):
        self._pause.set()

    # ── checkpoint ────────────────────────────────────────────────────────
    def _ckpt_path(self) -> Path:
        return cfg.CHECKPOINT_DIR / f"{self.session_id}.json"

    def save_checkpoint(self, wave_idx: int):
        data = {
            "wave_idx": wave_idx,
            "stats": self.stats.to_dict(),
            "seen": list(self._seen),
            "live": [lv.__dict__ for lv in self._live],
        }
        self._ckpt_path().write_bytes(orjson.dumps(data))

    def load_checkpoint(self) -> int:
        p = self._ckpt_path()
        if not p.exists():
            return 0
        data = orjson.loads(p.read_bytes())
        self._seen = set(data.get("seen", []))
        self._live = [LiveUrl(**x) for x in data.get("live", [])]
        st = data.get("stats", {})
        self.stats.raw = st.get("raw", 0)
        self.stats.unique = st.get("unique", 0)
        self.stats.live = st.get("live", 0)
        self.stats.filtered = st.get("filtered", 0)
        self.stats.dorks_used = st.get("dorks_used", 0)
        self.stats.errors = st.get("errors", 0)
        return int(data.get("wave_idx", 0))

    # ── single dork ───────────────────────────────────────────────────────
    async def _process_dork(self, dork: str):
        if self._stop.is_set():
            return
        await self._pause.wait()

        proxy = await self.proxy_pool.next()
        async with self._sem:
            try:
                async with make_client(proxy) as client:
                    params = {**cfg.DEFAULT_PARAMS, "p": dork}
                    r = await client.get(cfg.YAHOO_JP_SEARCH, params=params)
                    if r.status_code != 200:
                        self.stats.errors += 1
                        await self.proxy_pool.report_fail(proxy)
                        return
                    await self.proxy_pool.report_ok(proxy)
                    serp = extract_yahoo_jp(r.text, dork)
                    self.stats.raw += len(serp)

                    for item in serp:
                        norm = item.url.split("#")[0].rstrip("/").lower()
                        if norm in self._seen:
                            continue
                        self._seen.add(norm)
                        self.stats.unique += 1

                        live = await live_check(client, item.url, dork)
                        if live:
                            async with self._lock:
                                self._live.append(live)
                                self.stats.live += 1
                        else:
                            self.stats.filtered += 1
            except Exception as e:
                self.stats.errors += 1
                await self.proxy_pool.report_fail(proxy)
                logger.debug(f"dork fail [{dork[:40]}]: {e}")

        self.stats.dorks_used += 1

    # ── run ───────────────────────────────────────────────────────────────
    async def run(self, resume: bool = True):
        start_wave = self.load_checkpoint() if resume else 0
        waves = [
            self.dorks[i : i + cfg.WAVE_SIZE]
            for i in range(0, len(self.dorks), cfg.WAVE_SIZE)
        ]

        for wi, wave in enumerate(waves):
            if wi < start_wave:
                continue
            if self._stop.is_set():
                break

            tasks = [asyncio.create_task(self._process_dork(d)) for d in wave]
            done = 0
            for coro in asyncio.as_completed(tasks):
                await coro
                done += 1
                if done % cfg.PROGRESS_EVERY == 0 and self.on_progress:
                    try:
                        await self.on_progress(self.stats)
                    except Exception:
                        pass

            self.save_checkpoint(wi + 1)
            if self.on_progress:
                try:
                    await self.on_progress(self.stats)
                except Exception:
                    pass

        return self._live

    # ── export ────────────────────────────────────────────────────────────
    def export(self, fmt: str = "txt") -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        if fmt == "json":
            path = cfg.RESULTS_DIR / f"live_{self.session_id}_{ts}.json"
            path.write_bytes(orjson.dumps([lv.__dict__ for lv in self._live], option=orjson.OPT_INDENT_2))
        elif fmt == "csv":
            import csv
            path = cfg.RESULTS_DIR / f"live_{self.session_id}_{ts}.csv"
            with path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["url", "status", "title", "dork", "content_length", "final_url"])
                w.writeheader()
                for lv in self._live:
                    w.writerow(lv.__dict__)
        else:
            path = cfg.RESULTS_DIR / f"live_{self.session_id}_{ts}.txt"
            path.write_text("\n".join(lv.url for lv in self._live), encoding="utf-8")
        return path
