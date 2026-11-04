# SPEC-PRICE-001 — Product price range filter

## Status
Active (must be applied by the verification task)

## Goal
`filter_products` in `sample_app/catalog.py` must support inclusive numeric
price bounds so storefront APIs can filter by budget.

## Requirements
1. Accept optional keyword arguments `min_price: float | None` and
   `max_price: float | None`.
2. When `min_price` is set, keep only products with `price >= min_price`.
3. When `max_price` is set, keep only products with `price <= max_price`.
4. When both are set, keep the inclusive intersection.
5. Existing `category` exact-match filter must keep working unchanged.
6. Products missing a numeric `price` field must be excluded when any price
   bound is active.

## Non-goals
- Currency conversion
- Fuzzy category match
- Sorting changes

## Related code
- `sample_app/catalog.py` :: `filter_products`

## Related tests
- `sample_app/tests/test_catalog.py` :: `test_price_range_inclusive`
- `sample_app/tests/test_catalog.py` :: `test_price_only_min`
- `sample_app/tests/test_catalog.py` :: `test_empty_when_no_match`
- Regression: `test_category_filter`
