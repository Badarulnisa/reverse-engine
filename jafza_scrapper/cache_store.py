"""
Persistent cache for Google Places lookups, keyed by a normalized
version of the company name (plus geography hint, since the same raw
name could in principle be queried under a different hint later).

Using SQLite rather than a flat JSON file so partial writes from a
mid-run crash don't corrupt the whole cache -- each commit is atomic.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from matcher import MatchResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS lookups (
    cache_key TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    result_json TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


class PlacesCache:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute(SCHEMA)
        self.conn.commit()

    @staticmethod
    def make_key(company_name: str, geography_hint: str) -> str:
        norm = " ".join(company_name.strip().upper().split())
        return f"{norm}::{geography_hint}"

    def get(self, company_name: str, geography_hint: str) -> Optional[MatchResult]:
        key = self.make_key(company_name, geography_hint)
        row = self.conn.execute(
            "SELECT result_json FROM lookups WHERE cache_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return MatchResult(**data)

    def put(self, company_name: str, geography_hint: str, result: MatchResult) -> None:
        key = self.make_key(company_name, geography_hint)
        self.conn.execute(
            "INSERT OR REPLACE INTO lookups (cache_key, company_name, result_json, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            (key, company_name, json.dumps(asdict(result)), time.time()),
        )
        self.conn.commit()

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM lookups").fetchone()[0]

    def close(self) -> None:
        self.conn.close()