"""
OpenAI Status Monitor (Async + Efficient)

- Polls OpenAI status Atom feed
- Uses ETag + Last-Modified (bandwidth efficient)
- Deduplicates entries
- Prints only new relevant incidents
"""

import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from typing import List, Dict, Optional

import aiohttp
import feedparser


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


# ==============================
# Feed Client
# ==============================

class StatusFeedClient:
    """
    Responsible for:
    - Fetching feed using conditional GET
    - Tracking ETag / Last-Modified
    - Avoiding duplicate entries
    """

    def __init__(self, feed_url: str, session: aiohttp.ClientSession):
        self.feed_url = feed_url
        self.session = session
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._seen_ids: set[str] = set()

    async def fetch_new_entries(self) -> List[feedparser.FeedParserDict]:
        """
        Fetch feed.
        Returns only new entries that haven't been seen before.
        """

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
                    print(
                        f"[WARN] Unexpected HTTP {response.status}",
                        file=sys.stderr,
                    )
                    return []

                self._etag = response.headers.get("ETag")
                self._last_modified = response.headers.get("Last-Modified")

                raw_body = await response.text()

        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f"[WARN] Fetch failed: {exc}", file=sys.stderr)
            return []

        parsed_feed = feedparser.parse(raw_body)
        return self._extract_new(parsed_feed.entries)

    def _extract_new(self, entries: List[feedparser.FeedParserDict]):
        """
        Deduplicate entries based on ID or link.
        """

        new_items = []

        for entry in entries:
            entry_id = entry.get("id") or entry.get("link")
            if not entry_id:
                continue

            if entry_id in self._seen_ids:
                continue

            self._seen_ids.add(entry_id)
            new_items.append(entry)

        return new_items


# ==============================
# Parsing Helpers
# ==============================

def clean_html(text: str) -> str:
    """
    Remove HTML tags and normalize whitespace.
    """
    no_tags = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def extract_incident_data(entry: feedparser.FeedParserDict) -> Dict:
    """
    Convert feed entry into structured dict.
    """

    title = entry.get("title", "Unknown")
    summary = clean_html(entry.get("summary", "No details"))

    updated_struct = (
        entry.get("updated_parsed") or entry.get("published_parsed")
    )

    if updated_struct:
        dt = datetime(*updated_struct[:6], tzinfo=timezone.utc)
    else:
        dt = datetime.now(timezone.utc)

    timestamp = dt.strftime("%Y-%m-%d %H:%M:%S")

    # Try to split: "Product - Status"
    parts = title.split(" - ", 1)
    product = parts[0].strip()
    status = parts[1].strip() if len(parts) > 1 else ""

    return {
        "timestamp": timestamp,
        "product": product,
        "status": status,
        "summary": summary[:600],
        "url": entry.get("link", ""),
    }


def is_incident(entry: feedparser.FeedParserDict) -> bool:
    """
    Check whether entry contains incident-related keywords.
    """
    content = (
        (entry.get("title") or "") +
        " " +
        (entry.get("summary") or "")
    ).lower()

    return any(keyword in content for keyword in INCIDENT_KEYWORDS)


def display_incident(data: Dict):
    """
    Print formatted incident details.
    """

    line = "=" * 70
    print(f"\n{line}")
    print(f"[{data['timestamp']} UTC] {data['product']}")

    if data["status"]:
        print(f"Status   : {data['status']}")

    print(f"Details  : {data['summary']}")

    if data["url"]:
        print(f"Link     : {data['url']}")

    print(line)


# ==============================
# Monitor Logic
# ==============================

async def run_monitor(feed_url: str, interval: int):
    """
    Main loop:
    - Warm up (baseline load)
    - Poll periodically
    - Print new incidents only
    """

    print(f"[INFO] Monitoring: {feed_url}")
    print(f"[INFO] Poll interval: {interval}s\n")

    connector = aiohttp.TCPConnector(limit=20)

    async with aiohttp.ClientSession(connector=connector) as session:

        client = StatusFeedClient(feed_url, session)

        # Baseline load
        existing = await client.fetch_new_entries()
        print(f"[INFO] Baseline loaded: {len(existing)} entries stored.")
        print("[INFO] Waiting for new updates...\n")

        while True:
            await asyncio.sleep(interval)

            now = datetime.now(timezone.utc).strftime("%H:%M:%S")
            new_entries = await client.fetch_new_entries()

            if not new_entries:
                print(f"[{now} UTC] No updates.")
                continue

            for entry in new_entries:
                if is_incident(entry):
                    display_incident(extract_incident_data(entry))
                else:
                    print(f"[{now} UTC] New entry (non-incident): {entry.get('title')}")


# ==============================
# Entry Point
# ==============================

if __name__ == "__main__":

    try:
        asyncio.run(run_monitor(STATUS_FEED_URL, POLL_INTERVAL))
    except KeyboardInterrupt:
        print("\n[INFO] Monitor stopped.")
