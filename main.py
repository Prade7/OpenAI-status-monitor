"""
OpenAI Status Monitor — FastAPI Web App
========================================
Runs a background poller + exposes incidents via HTTP endpoints.

Routes:
  GET /                  → HTML dashboard (browser friendly)
  GET /status            → latest status summary (JSON)
  GET /incidents         → all collected incidents (JSON)
  GET /incidents/latest  → most recent incident only (JSON)
"""

import asyncio
import os
import re
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import List, Dict, Optional

import aiohttp
import feedparser
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse


# ==============================
# Config
# ==============================

STATUS_FEED_URL = os.getenv(
    "STATUS_FEED_URL",
    "https://status.openai.com/history.atom",
)

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "60"))

INCIDENT_KEYWORDS = {
    "investigating",
    "identified",
    "monitoring",
    "degraded",
    "outage",
    "incident",
    "disruption",
    "resolved",
}

# In-memory store — holds all incidents collected since startup
incident_store: List[Dict] = []
last_polled_at: Optional[str] = None


# ==============================
# Feed Client
# ==============================

class StatusFeedClient:
    def __init__(self, feed_url: str, session: aiohttp.ClientSession):
        self.feed_url = feed_url
        self.session = session
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._seen_ids: set[str] = set()

    async def fetch_new_entries(self) -> List[feedparser.FeedParserDict]:
        headers = {
            "User-Agent": "status-monitor/2.0",
            "Accept": "application/atom+xml, application/rss+xml, */*",
        }
        if self._etag:
            headers["If-None-Match"] = self._etag
        if self._last_modified:
            headers["If-Modified-Since"] = self._last_modified

        try:
            async with self.session.get(
                self.feed_url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status == 304:
                    return []
                if response.status != 200:
                    print(f"[WARN] HTTP {response.status}", file=sys.stderr)
                    return []
                self._etag = response.headers.get("ETag")
                self._last_modified = response.headers.get("Last-Modified")
                raw_body = await response.text()

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[WARN] Fetch failed: {exc}", file=sys.stderr)
            return []

        parsed_feed = feedparser.parse(raw_body)
        return self._extract_new(parsed_feed.entries)

    def _extract_new(self, entries):
        new_items = []
        for entry in entries:
            entry_id = entry.get("id") or entry.get("link")
            if not entry_id or entry_id in self._seen_ids:
                continue
            self._seen_ids.add(entry_id)
            new_items.append(entry)
        return new_items


# ==============================
# Parsing Helpers
# ==============================

def clean_html(text: str) -> str:
    no_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def extract_incident_data(entry: feedparser.FeedParserDict) -> Dict:
    title = entry.get("title", "Unknown")
    summary = clean_html(entry.get("summary", "No details"))
    updated_struct = entry.get("updated_parsed") or entry.get("published_parsed")

    if updated_struct:
        dt = datetime(*updated_struct[:6], tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    parts = title.split(" - ", 1)
    return {
        "timestamp": dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "product": parts[0].strip(),
        "status": parts[1].strip() if len(parts) > 1 else "",
        "summary": summary[:600],
        "url": entry.get("link", ""),
    }


def is_incident(entry: feedparser.FeedParserDict) -> bool:
    content = ((entry.get("title") or "") + " " + (entry.get("summary") or "")).lower()
    return any(kw in content for kw in INCIDENT_KEYWORDS)


# ==============================
# Background Poller
# ==============================

async def poll_loop():
    """Runs forever in the background, populating incident_store."""
    global last_polled_at

    print(f"[INFO] Poller started — {STATUS_FEED_URL} every {POLL_INTERVAL}s")

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=20)) as session:
        client = StatusFeedClient(STATUS_FEED_URL, session)

        # Baseline: absorb existing entries silently
        existing = await client.fetch_new_entries()
        # Include all existing entries in the store so /incidents is populated on first hit
        for entry in existing:
            incident_store.append(extract_incident_data(entry))
        incident_store.reverse()  # newest first
        last_polled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"[INFO] Baseline: {len(existing)} entries loaded.")

        while True:
            await asyncio.sleep(POLL_INTERVAL)
            last_polled_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

            new_entries = await client.fetch_new_entries()
            if not new_entries:
                print(f"[{last_polled_at}] No new updates.")
                continue

            for entry in new_entries:
                if is_incident(entry):
                    data = extract_incident_data(entry)
                    incident_store.insert(0, data)  # prepend — newest first
                    print(f"[NEW INCIDENT] {data['product']} — {data['status']}")


# ==============================
# FastAPI App
# ==============================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start background poller when app boots."""
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()


app = FastAPI(
    title="OpenAI Status Monitor",
    description="Live tracker for OpenAI API incidents and outages.",
    version="2.0.0",
    lifespan=lifespan,
)


# ==============================
# Routes
# ==============================

@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
async def dashboard():
    """Browser-friendly HTML dashboard showing all incidents."""

    rows = ""
    for inc in incident_store:
        status_color = (
            "#ef4444" if any(k in inc["status"].lower() for k in ["outage", "degraded", "investigating"])
            else "#22c55e" if "resolved" in inc["status"].lower()
            else "#f59e0b"
        )
        rows += f"""
        <tr>
            <td>{inc['timestamp']}</td>
            <td>{inc['product']}</td>
            <td><span style="color:{status_color};font-weight:600">{inc['status'] or 'Update'}</span></td>
            <td>{inc['summary'][:150]}{'...' if len(inc['summary']) > 150 else ''}</td>
            <td><a href="{inc['url']}" target="_blank">🔗 Link</a></td>
        </tr>
        """

    if not rows:
        rows = '<tr><td colspan="5" style="text-align:center;color:#888">No incidents detected yet. Check back soon.</td></tr>'

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8"/>
        <meta http-equiv="refresh" content="60"/>
        <title>OpenAI Status Monitor</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                   background: #0f172a; color: #e2e8f0; padding: 2rem; }}
            h1 {{ font-size: 1.8rem; margin-bottom: 0.25rem; }}
            .meta {{ color: #94a3b8; font-size: 0.85rem; margin-bottom: 1.5rem; }}
            .badge {{ display:inline-block; background:#1e293b; border:1px solid #334155;
                     padding:0.2rem 0.6rem; border-radius:999px; font-size:0.75rem; margin-left:0.5rem; }}
            table {{ width: 100%; border-collapse: collapse; background: #1e293b;
                    border-radius: 0.75rem; overflow: hidden; }}
            th {{ background: #0f172a; padding: 0.75rem 1rem; text-align: left;
                 font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em;
                 color: #64748b; }}
            td {{ padding: 0.85rem 1rem; border-top: 1px solid #334155;
                 font-size: 0.875rem; vertical-align: top; }}
            tr:hover td {{ background: #263148; }}
            a {{ color: #60a5fa; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
            .api-links {{ margin-top: 1.5rem; display: flex; gap: 1rem; flex-wrap: wrap; }}
            .api-links a {{ background: #1e293b; border: 1px solid #334155; padding: 0.4rem 1rem;
                           border-radius: 0.5rem; color: #94a3b8; font-size: 0.8rem; }}
            .api-links a:hover {{ color: #e2e8f0; border-color: #60a5fa; text-decoration:none; }}
        </style>
    </head>
    <body>
        <h1>🟢 OpenAI Status Monitor</h1>
        <p class="meta">
            Tracking: <strong>{STATUS_FEED_URL}</strong>
            <span class="badge">Last polled: {last_polled_at or 'Loading...'}</span>
            <span class="badge">Auto-refreshes every 60s</span>
        </p>
        <table>
            <thead>
                <tr>
                    <th>Timestamp</th>
                    <th>Product</th>
                    <th>Status</th>
                    <th>Details</th>
                    <th>Link</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
        <div class="api-links">
            <a href="/incidents">📦 JSON — All Incidents</a>
            <a href="/incidents/latest">⚡ JSON — Latest Incident</a>
            <a href="/status">🔍 JSON — Status Summary</a>
            <a href="/docs">📖 API Docs</a>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html)


@app.get("/status", tags=["API"])
async def get_status():
    """Returns current monitor status and summary counts."""
    return JSONResponse({
        "monitor": "OpenAI Status Monitor",
        "feed_url": STATUS_FEED_URL,
        "last_polled_at": last_polled_at,
        "total_incidents": len(incident_store),
        "latest_incident": incident_store[0] if incident_store else None,
    })


@app.get("/incidents", tags=["API"])
async def get_all_incidents():
    """Returns all collected incidents, newest first."""
    return JSONResponse({
        "total": len(incident_store),
        "last_polled_at": last_polled_at,
        "incidents": incident_store,
    })


@app.get("/incidents/latest", tags=["API"])
async def get_latest_incident():
    """Returns the single most recent incident."""
    if not incident_store:
        return JSONResponse({"message": "No incidents detected yet."}, status_code=404)
    return JSONResponse(incident_store[0])
