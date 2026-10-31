# Venom Deep Parser — Yahoo Japan

Personal Telegram bot + high-concurrency harvester.  
No dork generation. No license. No keys.  
Designed for Kaggle 2×T4 or any high-concurrency host.

## What it does

1. You upload a ready dork list (txt / csv)
2. Optional proxy list
3. Start → concurrent Yahoo Japan SERP scrape
4. Extract results → live-check every URL
5. Dedup + soft-404 filter
6. Live progress in Telegram (raw / unique / live / filtered / speed)
7. Checkpoint every wave → safe resume
8. Download live URLs as txt / json / csv

## Files

| file            | role                                      |
|-----------------|-------------------------------------------|
| `bot.py`        | Telegram interface (aiogram 3)            |
| `parser_core.py`| Async engine (httpx + selectolax)         |
| `run_cli.py`    | Headless / Kaggle runner                  |
| `config.py`     | All tunables                              |
| `requirements.txt` | deps                                   |

## Quick start (Telegram)

```bash
pip install -r requirements.txt
# put your token in config.py or export BOT_TOKEN=...
python bot.py
```

Open the bot → `/start` → Upload Dorks → Start Deep Parser.

## Kaggle / CLI (2×T4)

```bash
python run_cli.py -d dorks.txt -p proxies.txt -o json --session wave1
```

On Kaggle notebook you can also:

```python
!pip install -r requirements.txt
!python run_cli.py -d /kaggle/input/your-dorks/dorks.txt -o txt
```

Increase `MAX_CONCURRENT_REQUESTS` in `config.py` according to your proxy quality.  
120 is aggressive but safe with a healthy proxy pool.

## Proxy format

```
http://user:pass@host:port
http://host:port
socks5://user:pass@host:port
```

## Checkpoint

State is saved under `data/checkpoints/`.  
Kill the process and re-run with the same session id → continues from last completed wave.

## Notes for speed on 2×T4

- The bottleneck is network, not GPU. GPU is unused unless you later add embedding ranking.
- Use many cheap residential / mobile proxies.
- `WAVE_SIZE = 250` + `PROGRESS_EVERY = 25` keeps Telegram responsive.
- `uvloop` is installed automatically on Linux for lower event-loop overhead.
