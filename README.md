# 🟢 OpenAI Status Tracker

A lightweight, async Python script that monitors the **OpenAI status page** for new incidents, outages, and service degradations — and prints them to the console in real time.

```
======================================================================
[2025-11-03 14:32:00 UTC] API - Chat Completions
Status   : Degraded Performance
Details  : We are investigating reports of elevated error rates affecting
           Chat Completions. Our team is actively working to resolve this.
Link     : https://status.openai.com/incidents/abc123
======================================================================
```

---

## Features

- **Efficient polling** — uses HTTP `ETag` / `Last-Modified` conditional requests so the server returns HTTP `304 Not Modified` (zero bytes) when nothing has changed
- **Structured parsing** — reads the official Atom feed from Atlassian Statuspage (the platform OpenAI uses), not fragile HTML scraping
- **Deduplication** — a `seen_ids` set ensures the same incident is never printed twice
- **Baseline awareness** — on startup, existing entries are silently absorbed so you only get alerted about *new* incidents
- **Scalable** — built on `asyncio` + `aiohttp`; tracking 100+ status pages simultaneously just means launching 100+ coroutines in one event loop
- **Jupyter compatible** — auto-detects a running event loop and applies `nest_asyncio` automatically

---

## Requirements

- Python 3.10+

---

## Installation

```bash
git clone https://github.com/your-username/openai-status-monitor.git
cd openai-status-monitor
pip install -r requirements.txt
```

---

## Usage

### Standard Python

```bash
python openai_status_tracker.py
```

### Jupyter / IPython Notebook

```python
import nest_asyncio
nest_asyncio.apply()

import asyncio
asyncio.run(run_monitor(STATUS_FEED_URL, POLL_INTERVAL))
```

---

## Configuration

| Variable          | Default                                    | Description                |
|-------------------|--------------------------------------------|----------------------------|
| `POLL_INTERVAL`   | `60`                                       | Seconds between each poll  |
| `STATUS_FEED_URL` | `https://status.openai.com/history.atom`   | Atom feed URL to monitor   |

```bash
POLL_INTERVAL=30 python openai_status_tracker.py
```

---

## Tracking Multiple Providers

```python
FEEDS = [
    "https://status.openai.com/history.atom",
    "https://www.githubstatus.com/history.atom",
    "https://status.anthropic.com/history.atom",
]

async def monitor_many():
    await asyncio.gather(*(run_monitor(url, POLL_INTERVAL) for url in FEEDS))

asyncio.run(monitor_many())
```

---

## Project Structure

```
openai-status-monitor/
├── openai_status_tracker.py   # Main script
├── requirements.txt           # Python dependencies
└── README.md                  # This file
```

---

## License

MIT License — free to use, modify, and distribute.
