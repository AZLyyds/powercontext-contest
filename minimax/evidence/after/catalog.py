"""Tiny product catalog used as the MiniMax SDD verification target.

BEFORE patch: ``min_price`` / ``max_price`` are accepted but ignored
(tests that require price filtering FAIL).
AFTER MiniMax patch: inclusive price range filtering must work.
"""

from __future__ import annotations

from typing import Any


def filter_products(
    products: list[dict[str, Any]],
    *,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
) -> list[dict[str, Any]]:
    """Return products matching the given filters.

    Spec SPEC-PRICE-001 requires inclusive ``min_price`` / ``max_price``
    filtering. Category filter is case-sensitive exact match when provided.
    """
    result = products
    if category is not None:
        result = [p for p in result if p.get("category") == category]
    if min_price is not None or max_price is not None:
        filtered = []
        for p in result:
            price = p.get("price")
            if not isinstance(price, (int, float)):
                continue
            if min_price is not None and price < min_price:
                continue
            if max_price is not None and price > max_price:
                continue
            filtered.append(p)
        result = filtered
    return result


def demo_catalog() -> list[dict[str, Any]]:
    return [
        {"id": 1, "name": "seekdb mug", "category": "merch", "price": 39.0},
        {"id": 2, "name": "OceanBase hoodie", "category": "merch", "price": 199.0},
        {"id": 3, "name": "latency sticker", "category": "merch", "price": 9.0},
        {"id": 4, "name": "vector notebook", "category": "stationery", "price": 25.0},
        {"id": 5, "name": "SQL cheat sheet", "category": "stationery", "price": 15.0},
    ]
