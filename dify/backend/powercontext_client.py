"""
把记忆读写转到真实 PowerContext（POWERCONTEXT_MODE=http）。

环境变量：
  POWERCONTEXT_BASE_URL   例如 http://127.0.0.1:8000
  POWERCONTEXT_API_KEY    若网关需要（勿提交真实密钥）
  POWERCONTEXT_SCOPE      逻辑 scope / source 名（默认 grudge-npc）

预期路径（以实现为准，请对照你部署的 OpenAPI）：
  POST {BASE}/v1/sources/content          写入记忆片段
  POST {BASE}/v1/sources/content/search   召回（404 时返回 TODO 提示）
"""

from __future__ import annotations

import os
from typing import Any

import httpx


class PowerContextMemoryClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        scope: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.getenv("POWERCONTEXT_BASE_URL") or "").rstrip(
            "/"
        )
        self.api_key = api_key or os.getenv("POWERCONTEXT_API_KEY") or ""
        self.scope = (
            scope
            or os.getenv("POWERCONTEXT_SCOPE")
            or os.getenv("POWERCONTEXT_SOURCE_ID")
            or "grudge-npc"
        )
        if not self.base_url:
            raise ValueError(
                "POWERCONTEXT_MODE=http 需要设置 POWERCONTEXT_BASE_URL"
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def save(
        self,
        *,
        session_id: str,
        event: str,
        severity: int = 1,
        tags: list[str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "scope": self.scope,
            "title": f"grudge:{session_id}",
            "content": event,
            "metadata": {
                "kind": "grudge",
                "session_id": session_id,
                "severity": severity,
                "tags": tags or [],
                **(meta or {}),
            },
        }
        url = f"{self.base_url}/v1/sources/content"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json() if resp.content else {"ok": True, "proxied": payload}
            if isinstance(data, dict):
                data.setdefault("event", event)
                data.setdefault("session_id", session_id)
                data.setdefault("severity", severity)
                data.setdefault("tags", tags or [])
            return data

    async def recall(
        self,
        *,
        session_id: str,
        query: str = "",
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        url = f"{self.base_url}/v1/sources/content/search"
        payload = {
            "scope": self.scope,
            "session_id": session_id,
            "query": query,
            "limit": limit,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=self._headers())
            if resp.status_code == 404:
                return [
                    {
                        "id": "todo",
                        "event": (
                            "TODO: 请将 recall URL 改为真实 PowerContext 接口；"
                            f"当前尝试 {url} 返回 404"
                        ),
                        "severity": 1,
                        "tags": ["todo"],
                    }
                ]
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            return data.get("items") or data.get("results") or data.get("grudges") or []
