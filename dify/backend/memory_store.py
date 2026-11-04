"""本地 JSON 文件记忆存储。"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonMemoryStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        if not self.path.exists():
            self._write({"sessions": {}})

    def _read(self) -> dict[str, Any]:
        with self.path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.path)

    def save(
        self,
        *,
        session_id: str,
        event: str,
        severity: int = 1,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "event": event,
            "severity": max(1, min(int(severity), 5)),
            "tags": tags or [],
            "meta": meta or {},
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            data = self._read()
            sessions = data.setdefault("sessions", {})
            bucket = sessions.setdefault(session_id, {"grudges": []})
            bucket["grudges"].append(record)
            self._write(data)
        return record

    def recall(
        self,
        *,
        session_id: str,
        query: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        with self._lock:
            data = self._read()
            grudges = list(
                data.get("sessions", {}).get(session_id, {}).get("grudges", [])
            )

        q = (query or "").strip().lower()
        if q:
            scored: list[tuple[int, dict[str, Any]]] = []
            for g in grudges:
                text = f"{g.get('event', '')} {' '.join(g.get('tags') or [])}".lower()
                score = sum(1 for token in q.split() if token and token in text)
                score += int(g.get("severity") or 0)
                scored.append((score, g))
            scored.sort(key=lambda x: (x[0], x[1].get("created_at", "")), reverse=True)
            return [g for s, g in scored if s > 0][:limit] or grudges[-limit:][::-1]

        return list(reversed(grudges))[:limit]
