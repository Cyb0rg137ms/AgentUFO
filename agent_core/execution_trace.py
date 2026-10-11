"""Append-only, JSONL execution events with conservative secret redaction."""
from __future__ import annotations
import json, os, re, threading, time, uuid
from pathlib import Path
from typing import Any, Mapping, Optional

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*([=:])\s*([^\s,;]+)"),
    re.compile(r"\b(sk-[A-Za-z0-9_-]{12,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b"),
]

def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): ("[REDACTED]" if re.search(r"(?i)(api.?key|token|password|secret|credential)", str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        out = value
        for pattern in _SECRET_PATTERNS:
            if pattern.groups == 3:
                out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", out)
            else:
                out = pattern.sub("[REDACTED]", out)
        return out[:12000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:2000]

class ExecutionTrace:
    """Writes one redacted event per line; never logs prompt bodies by default."""
    def __init__(self, path: str | Path, run_id: Optional[str] = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex
        self._lock = threading.Lock()

    def emit(self, event: str, *, task_id: Optional[str] = None,
             fields: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        record = {"ts": time.time(), "run_id": self.run_id, "event": event,
                  "task_id": task_id, "fields": redact(fields or {})}
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return record

    def read_events(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        events = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                try: events.append(json.loads(line))
                except json.JSONDecodeError: continue
        return events
