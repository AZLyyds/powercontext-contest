"""CLI for the MiniMax SDD context module.

Examples:
  python -m sdd_context.cli ingest
  python -m sdd_context.cli context --task "implement SPEC-PRICE-001 price filter"
  python -m sdd_context.cli run-sample
  python -m sdd_context.cli resume --task-id price-001
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sdd_context.module import SDDContextModule, reset_sample_to_before  # noqa: E402

TASK_DEFAULT = (
    "Implement SPEC-PRICE-001 inclusive min_price max_price filter in sample_app/catalog.py"
)
TASK_ID = "price-001"


def _run_tests() -> tuple[int, str]:
    cmd = [sys.executable, "-m", "pytest", "sample_app/tests/test_catalog.py", "-v"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def cmd_ingest(_: argparse.Namespace) -> int:
    n = SDDContextModule().ingest_workspace()
    print(f"ingested {n} artifacts into seekdb/OB")
    return 0


def cmd_context(args: argparse.Namespace) -> int:
    mod = SDDContextModule()
    bundle = mod.build_context(args.task)
    print(json.dumps(
        {
            "task": bundle.task,
            "specs": [s["path"] for s in bundle.specs],
            "code": [c["path"] for c in bundle.code],
            "tests": [t["path"] for t in bundle.tests],
            "db_query_log": bundle.db_query_log,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


def cmd_run_sample(args: argparse.Namespace) -> int:
    mod = SDDContextModule()
    evidence = ROOT / "evidence"
    evidence.mkdir(exist_ok=True)

    print("== reset to pre-patch ==")
    reset_sample_to_before()
    # Capture canonical before snapshot
    before_src = (ROOT / "sample_app" / "catalog.py").read_text(encoding="utf-8")
    (evidence / "before").mkdir(exist_ok=True)
    (evidence / "before" / "catalog.py").write_text(before_src, encoding="utf-8")

    print("== tests BEFORE (expect FAIL on price cases) ==")
    code_b, log_b = _run_tests()
    (evidence / "before" / "pytest.log").write_text(log_b, encoding="utf-8")
    print(log_b)
    if code_b == 0:
        print("WARNING: pre-patch tests unexpectedly passed; catalog may already be patched")

    print("== ingest + build context ==")
    mod.ingest_workspace()
    bundle = mod.build_context(args.task)
    (evidence / "context_bundle.json").write_text(
        json.dumps(
            {
                "task": bundle.task,
                "specs": [s["path"] for s in bundle.specs],
                "code": [c["path"] for c in bundle.code],
                "tests": [t["path"] for t in bundle.tests],
                "db_query_log": bundle.db_query_log,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("context specs:", [s["path"] for s in bundle.specs])
    print("context code:", [c["path"] for c in bundle.code])
    print("context tests:", [t["path"] for t in bundle.tests])

    if args.dry_run_patch:
        print("== dry-run: applying known SPEC-PRICE-001 patch without MiniMax ==")
        _apply_reference_patch()
        record = {"mode": "dry_run_reference_patch", "note": "NOT valid for MiniMax scoring"}
        (evidence / "minimax_dev_record.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
    else:
        print("== MiniMax patch ==")
        record = mod.apply_minimax_patch(bundle)
        print("MiniMax summary:", record.get("summary"))

    print("== tests AFTER (expect PASS) ==")
    code_a, log_a = _run_tests()
    (evidence / "after").mkdir(exist_ok=True)
    (evidence / "after" / "pytest.log").write_text(log_a, encoding="utf-8")
    print(log_a)

    print("== PowerContext save ==")
    saved = mod.save_state(TASK_ID, bundle, extra={"after_tests_exit": code_a})
    print(json.dumps(saved, ensure_ascii=False, indent=2)[:800])

    print("== simulate restart + resume ==")
    mod2 = SDDContextModule()
    loaded = mod2.resume_state(TASK_ID)
    print("resumed keys:", list((loaded or {}).keys()))

    # Spec update check helper material
    print("== done; exit after_tests =", code_a)
    return 0 if code_a == 0 else 1


def _apply_reference_patch() -> None:
    path = ROOT / "sample_app" / "catalog.py"
    text = path.read_text(encoding="utf-8")
    needle = (
        "    # INTENTIONAL GAP (pre-patch): price bounds are ignored.\n"
        "    # MiniMax must implement inclusive filtering using min_price / max_price.\n"
        "    _ = (min_price, max_price)\n"
        "    return result\n"
    )
    repl = (
        "    if min_price is not None or max_price is not None:\n"
        "        filtered = []\n"
        "        for p in result:\n"
        "            price = p.get(\"price\")\n"
        "            if not isinstance(price, (int, float)):\n"
        "                continue\n"
        "            if min_price is not None and price < min_price:\n"
        "                continue\n"
        "            if max_price is not None and price > max_price:\n"
        "                continue\n"
        "            filtered.append(p)\n"
        "        result = filtered\n"
        "    return result\n"
    )
    if needle not in text:
        raise RuntimeError("pre-patch marker not found; reset sample first")
    path.write_text(text.replace(needle, repl, 1), encoding="utf-8")
    (ROOT / "evidence" / "after").mkdir(exist_ok=True)
    (ROOT / "evidence" / "after" / "catalog.py").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def cmd_resume(args: argparse.Namespace) -> int:
    loaded = SDDContextModule().resume_state(args.task_id)
    print(json.dumps(loaded, ensure_ascii=False, indent=2))
    return 0 if loaded else 1


def cmd_update_spec_demo(_: argparse.Namespace) -> int:
    """Mutate SPEC and re-ingest to show context follows updates."""
    spec = ROOT / "specs" / "SPEC-PRICE-001.md"
    text = spec.read_text(encoding="utf-8")
    marker = "\n## Verification marker\n"
    line = f"UPDATED_MARKER={__import__('datetime').datetime.utcnow().isoformat()}Z\n"
    if marker in text:
        head, _ = text.split(marker, 1)
        text = head + marker + line
    else:
        text = text.rstrip() + marker + line
    spec.write_text(text, encoding="utf-8")
    mod = SDDContextModule()
    mod.ingest_workspace()
    bundle = mod.build_context(TASK_DEFAULT)
    hit = next(s for s in bundle.specs if s["path"].endswith("SPEC-PRICE-001.md"))
    print("spec excerpt tail:")
    print(hit["content"][-200:])
    assert "UPDATED_MARKER=" in hit["content"]
    print("OK: context uses updated spec content")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="MiniMax SDD context module")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("ingest")
    s.set_defaults(func=cmd_ingest)

    s = sub.add_parser("context")
    s.add_argument("--task", default=TASK_DEFAULT)
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("run-sample")
    s.add_argument("--task", default=TASK_DEFAULT)
    s.add_argument(
        "--dry-run-patch",
        action="store_true",
        help="Apply local reference patch instead of MiniMax (not valid for scoring)",
    )
    s.set_defaults(func=cmd_run_sample)

    s = sub.add_parser("resume")
    s.add_argument("--task-id", default=TASK_ID)
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("update-spec-demo")
    s.set_defaults(func=cmd_update_spec_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
