"""Gravity memory collections, per person. Hosted they are gravity.memory's. Under `gravity run` the platform SDK is
not importable, so a stand-in keeps them in the folder the CLI names (GRAVITY_MEMORY_DIR, one `<name>.json` each,
which `gravity memory show` reads); under pytest a dict. Few calls a run: read a collection once with find(), write
what changed. (signal-leads' store.py, plus the local files.)"""

from __future__ import annotations

import json
import os
from pathlib import Path

from tooling import gravity


class _Local:
    def __init__(self, path: Path | None = None):
        self.path = path
        self.items: dict[str, dict] = {}
        if path and path.is_file():
            self.items = {k: v["value"] for k, v in json.loads(path.read_text(encoding="utf-8")).items()}

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({k: {"value": v} for k, v in self.items.items()}, ensure_ascii=False),
                                 encoding="utf-8")

    def get(self, key):
        return self.items.get(key)

    def put(self, key, value):
        self.items[key] = value
        self._save()

    def delete(self, key):
        self.items.pop(key, None)
        self._save()

    def find(self, where=None, limit=500):
        rows = [(k, v) for k, v in self.items.items() if all(v.get(a) == b for a, b in (where or {}).items())]
        return rows[-limit:][::-1]

    def has_many(self, keys):
        return [k for k in keys if k in self.items]


FIND_CAP = 500  # the platform's memory answers at most 500 rows per find and refuses a bigger ask (gateway memory.MAX_FIND);
                # a board past 500 jobs is cut at the oldest. ponytail: page by key ranges if anyone gets there
_LOCAL: dict[str, _Local] = {}


def collection(name: str):
    if gravity:
        return gravity.memory.collection(name)
    if name not in _LOCAL:
        folder = os.environ.get("GRAVITY_MEMORY_DIR")
        _LOCAL[name] = _Local(Path(folder) / f"{name}.json" if folder else None)
    return _LOCAL[name]


def put_safely(coll, key: str, value: dict) -> bool:
    """A write over the limit is reported, not fatal."""
    try:
        coll.put(key, value)
        return True
    except Exception as e:  # noqa: BLE001 - MemoryQuotaError hosted; nothing was written
        if type(e).__name__ != "MemoryQuotaError":
            raise
        return False
