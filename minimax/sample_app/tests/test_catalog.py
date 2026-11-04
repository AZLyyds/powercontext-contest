"""Functional + regression tests for the catalog filter task.

Pre-patch: test_price_range_inclusive FAILS (price filters ignored).
Post-patch: all tests PASS. Regression test_category_filter must keep passing.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalog import demo_catalog, filter_products  # noqa: E402


def test_category_filter() -> None:
    items = demo_catalog()
    got = filter_products(items, category="stationery")
    assert [p["id"] for p in got] == [4, 5]


def test_price_range_inclusive() -> None:
    items = demo_catalog()
    got = filter_products(items, category="merch", min_price=10.0, max_price=50.0)
    assert [p["id"] for p in got] == [1]


def test_price_only_min() -> None:
    items = demo_catalog()
    got = filter_products(items, min_price=100.0)
    assert [p["id"] for p in got] == [2]


def test_empty_when_no_match() -> None:
    items = demo_catalog()
    got = filter_products(items, category="merch", min_price=500.0)
    assert got == []


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
