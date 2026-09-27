"""操作台账（主线维护）。logs/operation_log.jsonl 追加写，可导出 CSV。"""
from __future__ import annotations

import csv
import json
import threading
from datetime import datetime
from pathlib import Path


class OpLog:
    _lock = threading.Lock()

    def __init__(self, dir_path: str | Path = "logs"):
        self.dir = Path(dir_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "operation_log.jsonl"

    def log(self, op: str, summary: dict | None = None):
        rec = {"time": datetime.now().isoformat(timespec="seconds"), "op": op}
        if summary:
            rec.update(summary)
        with self._lock:
            with open(self.jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        return rec

    def read_all(self, limit: int | None = None) -> list[dict]:
        if not self.jsonl.exists():
            return []
        rows = []
        with open(self.jsonl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        return rows[-limit:] if limit else rows

    def export_csv(self, path: str | Path) -> str:
        rows = self.read_all()
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        path = Path(path)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(keys)
            for r in rows:
                w.writerow([json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], (dict, list)) else r[k]
                            for k in keys])
        return str(path)

    def clear(self) -> int:
        """清空台账，返回删除的记录条数。"""
        with self._lock:
            n = 0
            if self.jsonl.exists():
                n = sum(1 for x in self.jsonl.read_text(encoding="utf-8").splitlines() if x.strip())
                self.jsonl.unlink()
            return n
