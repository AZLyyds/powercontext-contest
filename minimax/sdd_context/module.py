"""Context module entrypoints: ingest → select → patch → persist."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .db import ArtifactDB, ArtifactHit
from .minimax_client import MiniMaxClient
from .powercontext_client import PowerContextClient

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample_app"
SPECS = ROOT / "specs"
EVIDENCE = ROOT / "evidence"


@dataclass
class ContextBundle:
    task: str
    specs: list[dict[str, str]]
    code: list[dict[str, str]]
    tests: list[dict[str, str]]
    db_query_log: dict[str, Any]

    def as_prompt(self) -> str:
        parts = [f"TASK: {self.task}", "", "## Specs"]
        for s in self.specs:
            parts.append(f"### {s['path']}\n{s['content']}")
        parts.append("\n## Code to modify")
        for c in self.code:
            parts.append(f"### {c['path']}\n{c['content']}")
        parts.append("\n## Tests that must pass")
        for t in self.tests:
            parts.append(f"### {t['path']}\n{t['content']}")
        parts.append("\n## DB retrieval log")
        parts.append(json.dumps(self.db_query_log, ensure_ascii=False, indent=2))
        return "\n".join(parts)


class SDDContextModule:
    def __init__(self) -> None:
        self.db = ArtifactDB()
        self.pc = PowerContextClient()
        self.mm = MiniMaxClient()

    def ingest_workspace(self) -> int:
        self.db.init_schema()
        count = 0
        mapping = [
            (SPECS, "spec"),
            (SAMPLE, "code"),
            (SAMPLE / "tests", "test"),
        ]
        for base, kind in mapping:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in {".py", ".md", ".txt"}:
                    continue
                rel = str(path.relative_to(ROOT)).replace("\\", "/")
                text = path.read_text(encoding="utf-8")
                self.db.upsert(kind, rel, path.name, text)
                count += 1
        return count

    def build_context(self, task: str) -> ContextBundle:
        qlog = self.db.dump_query_log(task)
        hits = self.db.search(task, limit=12)
        # Guarantee required sources for the verification sample even if FT is weak.
        must = [
            ("spec", "specs/SPEC-PRICE-001.md"),
            ("code", "sample_app/catalog.py"),
            ("test", "sample_app/tests/test_catalog.py"),
        ]
        by_path = {h.path: h for h in hits}
        for kind, path in must:
            if path not in by_path:
                full = ROOT / path
                by_path[path] = ArtifactHit(
                    kind=kind,
                    path=path,
                    title=full.name,
                    content=full.read_text(encoding="utf-8"),
                    score_hint="must",
                )
        specs, code, tests = [], [], []
        for h in by_path.values():
            item = {"path": h.path, "title": h.title, "content": h.content, "score": h.score_hint}
            if h.kind == "spec" or h.path.startswith("specs/"):
                specs.append(item)
            elif h.kind == "test" or "/tests/" in h.path:
                tests.append(item)
            else:
                code.append(item)
        return ContextBundle(task=task, specs=specs, code=code, tests=tests, db_query_log=qlog)

    def apply_minimax_patch(self, bundle: ContextBundle, target_rel: str = "sample_app/catalog.py") -> dict[str, Any]:
        target = ROOT / target_rel
        before = target.read_text(encoding="utf-8")
        (EVIDENCE / "before" / "catalog.py").write_text(before, encoding="utf-8")
        result = self.mm.request_patch(bundle.as_prompt(), target_rel, before)
        parsed = result["parsed"]
        patched = parsed.get("patched_source")
        if not patched or not isinstance(patched, str):
            raise RuntimeError(f"MiniMax response missing patched_source: {result['raw'][:500]}")
        target.write_text(patched, encoding="utf-8")
        (EVIDENCE / "after" / "catalog.py").write_text(patched, encoding="utf-8")
        diff_path = EVIDENCE / "catalog.diff"
        diff_path.write_text(_unified_diff(before, patched, target_rel), encoding="utf-8")
        record = {
            "model": self.mm.model,
            "base_url": self.mm.base,
            "summary": parsed.get("summary"),
            "raw_response": result["raw"],
            "target": target_rel,
            "diff_file": str(diff_path.relative_to(ROOT)).replace("\\", "/"),
        }
        (EVIDENCE / "minimax_dev_record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record

    def save_state(self, task_id: str, bundle: ContextBundle, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        state = {
            "task": bundle.task,
            "required_specs": [s["path"] for s in bundle.specs],
            "required_code": [c["path"] for c in bundle.code],
            "required_tests": [t["path"] for t in bundle.tests],
            "done": ["ingest", "context_built", "minimax_patch"],
            "todo": ["run_tests", "review"],
            "extra": extra or {},
        }
        saved = self.pc.save_task_state(task_id, state)
        # Always mirror for evidence package
        mirror = EVIDENCE / "pc_state" / f"{task_id}.json"
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        (EVIDENCE / "powercontext_write.json").write_text(
            json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return saved

    def resume_state(self, task_id: str) -> dict[str, Any] | None:
        loaded = self.pc.load_task_state(task_id)
        (EVIDENCE / "powercontext_reuse.json").write_text(
            json.dumps({"task_id": task_id, "loaded": loaded}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return loaded


def _unified_diff(before: str, after: str, path: str) -> str:
    import difflib

    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def reset_sample_to_before() -> None:
    """Restore pre-patch catalog from evidence/before or the intentional gap template."""
    src = EVIDENCE / "before" / "catalog.py"
    dst = SAMPLE / "catalog.py"
    if src.exists():
        shutil.copyfile(src, dst)
        return
    # Ensure gap implementation is present (price args ignored).
    dst.write_text(
        (ROOT / "sample_app" / "catalog.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
