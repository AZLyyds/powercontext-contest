"""
记仇 NPC backend 自测（默认 file/json 模式，不依赖真实 PowerContext）。

用法（在 backend 目录）：
  python smoke_test.py

可选：对已启动的服务做在线探测
  python smoke_test.py --live http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path


def _run_inprocess() -> None:
    # 强制 file 模式，避免本机 .env 指向不可达的 PowerContext
    os.environ["POWERCONTEXT_MODE"] = "file"
    os.environ.pop("MEMORY_BACKEND", None)

    tmp = Path(tempfile.mkdtemp(prefix="grudge-smoke-")) / "grudges.json"
    os.environ["MEMORY_JSON_PATH"] = str(tmp)

    from fastapi.testclient import TestClient

    import main

    # 重新绑定到临时文件（main 已按环境变量初始化）
    from memory_store import JsonMemoryStore

    main.json_store = JsonMemoryStore(str(tmp))
    main.MODE = "file"
    main.pc_client = None

    client = TestClient(main.app)

    h = client.get("/health")
    assert h.status_code == 200, h.text
    body = h.json()
    assert body.get("ok") is True
    assert body.get("mode") == "file"
    print("[ok] /health", body)

    sid = "smoke-session-001"
    s = client.post(
        "/memory/save",
        json={
            "session_id": sid,
            "event": "玩家把NPC的药水藏起来了（恶作剧）",
            "severity": 4,
            "tags": ["prank", "potion"],
        },
    )
    assert s.status_code == 200, s.text
    saved = s.json()
    assert saved.get("ok") is True
    assert "药水" in saved["record"]["event"]
    print("[ok] /memory/save", saved["record"]["id"])

    r = client.post(
        "/memory/recall",
        json={"session_id": sid, "query": "药水", "limit": 5},
    )
    assert r.status_code == 200, r.text
    recalled = r.json()
    assert recalled.get("ok") is True
    assert len(recalled.get("grudges") or []) >= 1
    print("[ok] /memory/recall hits=", len(recalled["grudges"]))

    docs = client.get("/docs")
    assert docs.status_code == 200, docs.text
    print("[ok] /docs")


def _run_live(base: str) -> None:
    import httpx

    base = base.rstrip("/")
    with httpx.Client(timeout=10.0) as client:
        h = client.get(f"{base}/health")
        h.raise_for_status()
        print("[ok] live /health", h.json())

        sid = "smoke-live-001"
        s = client.post(
            f"{base}/memory/save",
            json={
                "session_id": sid,
                "event": "live smoke：门口挖坑整蛊",
                "severity": 3,
                "tags": ["prank"],
            },
        )
        s.raise_for_status()
        print("[ok] live /memory/save", s.json().get("record", {}).get("id"))

        r = client.post(
            f"{base}/memory/recall",
            json={"session_id": sid, "query": "挖坑", "limit": 5},
        )
        r.raise_for_status()
        print("[ok] live /memory/recall", r.json())


def main() -> int:
    parser = argparse.ArgumentParser(description="Grudge NPC memory smoke test")
    parser.add_argument(
        "--live",
        metavar="BASE_URL",
        help="对已启动服务做探测，例如 http://127.0.0.1:8765",
    )
    args = parser.parse_args()
    try:
        if args.live:
            _run_live(args.live)
        else:
            _run_inprocess()
    except Exception as exc:  # noqa: BLE001
        print("[FAIL]", exc, file=sys.stderr)
        return 1
    print("smoke_test PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
