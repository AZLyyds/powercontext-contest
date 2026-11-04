"""MiniMax OpenAI-compatible chat client (coding / patch generation)."""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any


class MiniMaxClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("MINIMAX_API_KEY") or os.getenv("MINIMAX_KEY") or ""
        # CN default; override with MINIMAX_BASE_URL=https://api.minimax.io/v1 for global.
        self.base = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1").rstrip("/")
        self.model = os.getenv("MINIMAX_MODEL", "MiniMax-M2")

    def available(self) -> bool:
        return bool(self.api_key)

    def chat(self, system: str, user: str) -> str:
        if not self.api_key:
            raise RuntimeError("MINIMAX_API_KEY is not set")
        body = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        req = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choice = data["choices"][0]["message"]["content"]
        return choice if isinstance(choice, str) else json.dumps(choice)

    def request_patch(self, context_blob: str, target_path: str, current_source: str) -> dict[str, Any]:
        system = (
            "You are MiniMax acting as a coding agent for Spec-Driven Development. "
            "Return ONLY a JSON object with keys: summary (string), patched_source (string). "
            "patched_source must be the full new file content for the target path. "
            "Implement SPEC-PRICE-001 inclusive min_price/max_price filtering. "
            "Do not wrap JSON in markdown."
        )
        user = (
            f"Target file: {target_path}\n\n"
            f"=== DEVELOPMENT CONTEXT (from seekdb + specs) ===\n{context_blob}\n\n"
            f"=== CURRENT FILE CONTENT ===\n{current_source}\n"
        )
        raw = self.chat(system, user)
        return {"raw": raw, "parsed": _parse_json_response(raw)}


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Heuristic: extract largest {...}
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise
