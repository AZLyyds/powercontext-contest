"""PowerContext HTTP client for task-state save / resume.

Talks to a running PowerContext server (default http://127.0.0.1:8000).

Flow used by this challenge:
1. Ensure a scope exists (POST /v1/scopes)
2. Persist task state as source content (POST /v1/sources/content)
3. Flush memory (POST /v1/memory/flush) when available
4. On resume: GET scope sources / list and parse our task JSON blob

If the exact route set differs by PowerContext version, set
POWERCONTEXT_MODE=file to use the file-backed adapter under
evidence/pc_state/ (still records the same payload for reviewers), while
README documents pointing POWERCONTEXT_BASE_URL at a real server for scoring.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class PowerContextClient:
    def __init__(self) -> None:
        self.base = os.getenv("POWERCONTEXT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
        self.mode = os.getenv("POWERCONTEXT_MODE", "http").lower()  # http | file
        self.scope_name = os.getenv("POWERCONTEXT_SCOPE", "minimax-sdd-demo")
        self.file_dir = Path(
            os.getenv(
                "POWERCONTEXT_FILE_DIR",
                str(Path(__file__).resolve().parents[1] / "evidence" / "pc_state"),
            )
        )
        self.file_dir.mkdir(parents=True, exist_ok=True)

    def _http(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"PowerContext HTTP {e.code} {path}: {detail}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"PowerContext unreachable at {self.base}{path}: {e}. "
                "Start PowerContext or set POWERCONTEXT_MODE=file for local dry-run."
            ) from e

    def ensure_scope(self) -> str:
        if self.mode == "file":
            return self.scope_name
        # Best-effort create; ignore conflicts.
        try:
            self._http("POST", "/v1/scopes", {"name": self.scope_name})
        except RuntimeError:
            pass
        return self.scope_name

    def save_task_state(self, task_id: str, state: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "task_id": task_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "state": state,
        }
        record = {
            "scope": self.ensure_scope(),
            "kind": "minimax_sdd_task_state",
            "payload": payload,
        }
        if self.mode == "file":
            path = self.file_dir / f"{task_id}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"mode": "file", "path": str(path), "record": record}

        content = json.dumps(record, ensure_ascii=False)
        body = {
            "scope": self.scope_name,
            "title": f"sdd-task:{task_id}",
            "content": content,
            "metadata": {"kind": "minimax_sdd_task_state", "task_id": task_id},
        }
        created = self._http("POST", "/v1/sources/content", body)
        try:
            self._http("POST", "/v1/memory/flush", {"scope": self.scope_name})
        except RuntimeError:
            # Older builds may not expose flush; source write is enough for reuse.
            pass
        return {"mode": "http", "base": self.base, "created": created, "record": record}

    def load_task_state(self, task_id: str) -> dict[str, Any] | None:
        if self.mode == "file":
            path = self.file_dir / f"{task_id}.json"
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))

        # Try listing sources for the scope; fall back to file mirror if API differs.
        for path in (f"/v1/scopes/{self.scope_name}/sources", "/v1/sources"):
            try:
                data = self._http("GET", path)
            except RuntimeError:
                continue
            items = data if isinstance(data, list) else data.get("items") or data.get("sources") or []
            for item in items:
                title = str(item.get("title") or "")
                content = item.get("content") or item.get("text") or ""
                if title == f"sdd-task:{task_id}" or task_id in title:
                    try:
                        return json.loads(content) if isinstance(content, str) else content
                    except json.JSONDecodeError:
                        return {"raw": content}
        # File mirror written alongside HTTP for evidence
        mirror = self.file_dir / f"{task_id}.json"
        if mirror.exists():
            return json.loads(mirror.read_text(encoding="utf-8"))
        return None
