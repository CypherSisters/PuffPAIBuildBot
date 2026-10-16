"""Short starting points Toolsmith is shown before it writes glue.

Each is a whole glue file that passes Drawbridge's ``glue`` checklist. The
local brain copies the closest one and changes the address, the arguments and
the field names; small models write better code from a working example than
from rules alone.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

PAGINATE = '''"""Every page of a list that comes in pages."""
import urllib.parse
import puff_glue

BASE = "https://api.example.org/items"


def items(query, pages=3):
    rows = []
    for page in range(1, int(pages) + 1):
        url = BASE + "?" + urllib.parse.urlencode({"q": query, "page": page})
        data = puff_glue.fetch_json(url)
        batch = data.get("results") or []
        for item in batch:
            rows.append({"title": item.get("name", ""), "link": item.get("url", "")})
        if not batch:
            break
    return rows
'''

KEY_HEADER = '''"""A service that needs a sign-up key. BuildBot adds the key from the vault
on the way out; the code only names the setting that holds it."""
import urllib.parse
import puff_glue

BASE = "https://api.example.org/v1/search"


def search(query):
    url = BASE + "?" + urllib.parse.urlencode({"q": query})
    data = puff_glue.fetch_json(url, key="service_key")
    return [{"title": d.get("title", ""), "when": d.get("date", "")}
            for d in data.get("items", [])]
'''

CSV_ROWS = '''"""A CSV file to rows."""
import puff_glue

URL = "https://data.example.org/latest.csv"


def latest(limit=100):
    text = puff_glue.fetch_text(URL, accept="text/csv")
    return puff_glue.csv_rows(text, limit=int(limit))
'''

RSS_ROWS = '''"""A news feed to rows."""
import puff_glue

URL = "https://news.example.org/feed.xml"


def headlines(limit=30):
    text = puff_glue.fetch_text(URL, accept="application/rss+xml")
    return puff_glue.rss_rows(text, limit=int(limit))
'''

LIBRARY = '''"""Call a library the plan installs (here: a made-up "tides" package)."""
import tides


def high_tides(lat, lon, days=2):
    rows = []
    for t in tides.predict(float(lat), float(lon), days=int(days)):
        rows.append({"when": str(t.time), "height_m": round(float(t.height), 2)})
    return rows
'''

ALL = {"paginate": PAGINATE, "key header": KEY_HEADER, "CSV to rows": CSV_ROWS,
       "RSS to rows": RSS_ROWS, "call a library": LIBRARY}

__all__ = ["ALL", "CSV_ROWS", "KEY_HEADER", "LIBRARY", "PAGINATE", "RSS_ROWS"]
