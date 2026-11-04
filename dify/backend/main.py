"""
记仇 NPC · 记忆小服务

POST /memory/save   记下玩家恶作剧
POST /memory/recall 翻旧账
GET  /health
GET  /docs          （FastAPI 自动文档）

模式（POWERCONTEXT_MODE）：
  file / json  — 本地 JSON（默认，评审可直接跑）
  http         — 转发到真实 PowerContext
兼容旧变量 MEMORY_BACKEND=json|powercontext。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from memory_store import JsonMemoryStore
from powercontext_client import PowerContextMemoryClient

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
JSON_PATH = os.getenv(
    "MEMORY_JSON_PATH",
    str(BASE_DIR / "data" / "grudges.json"),
)


def _resolve_mode() -> str:
    """返回 'file' 或 'http'。"""
    mode = (os.getenv("POWERCONTEXT_MODE") or "").strip().lower()
    if mode in ("http", "file", "json"):
        return "http" if mode == "http" else "file"
    legacy = (os.getenv("MEMORY_BACKEND") or "").strip().lower()
    if legacy in ("powercontext", "http"):
        return "http"
    return "file"


MODE = _resolve_mode()

app = FastAPI(title="Grudge NPC Memory", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

json_store = JsonMemoryStore(JSON_PATH)
pc_client: PowerContextMemoryClient | None = None
if MODE == "http":
    pc_client = PowerContextMemoryClient()


class SaveRequest(BaseModel):
    session_id: str = Field(..., description="对话 / 玩家会话 ID")
    event: str = Field(..., description="恶作剧或冒犯事件摘要")
    severity: int = Field(1, ge=1, le=5, description="记仇等级 1-5")
    tags: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class RecallRequest(BaseModel):
    session_id: str
    query: str = ""
    limit: int = Field(5, ge=1, le=50)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "mode": MODE,
        "backend": "powercontext" if MODE == "http" else "json",
        "json_path": JSON_PATH if MODE == "file" else None,
        "powercontext_base_url": os.getenv("POWERCONTEXT_BASE_URL")
        if MODE == "http"
        else None,
    }


@app.post("/memory/save")
async def memory_save(body: SaveRequest) -> dict[str, Any]:
    try:
        if MODE == "http" and pc_client is not None:
            saved = await pc_client.save(
                session_id=body.session_id,
                event=body.event,
                severity=body.severity,
                tags=body.tags,
                meta=body.meta,
            )
            return {"ok": True, "backend": "powercontext", "mode": "http", "record": saved}

        record = json_store.save(
            session_id=body.session_id,
            event=body.event,
            severity=body.severity,
            tags=body.tags,
            meta=body.meta,
        )
        return {"ok": True, "backend": "json", "mode": "file", "record": record}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/memory/recall")
async def memory_recall(body: RecallRequest) -> dict[str, Any]:
    try:
        if MODE == "http" and pc_client is not None:
            items = await pc_client.recall(
                session_id=body.session_id,
                query=body.query,
                limit=body.limit,
            )
            return {
                "ok": True,
                "backend": "powercontext",
                "mode": "http",
                "grudges": items,
            }

        items = json_store.recall(
            session_id=body.session_id,
            query=body.query,
            limit=body.limit,
        )
        return {"ok": True, "backend": "json", "mode": "file", "grudges": items}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8765"))
    # reload=False：评审一键启动更稳；开发可改 True
    uvicorn.run("main:app", host=host, port=port, reload=False)
