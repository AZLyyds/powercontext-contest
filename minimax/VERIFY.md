# 验证样例 — SPEC-PRICE-001 价格筛选

## 要改什么
在 `sample_app/catalog.py` 的 `filter_products` 中实现 inclusive `min_price` / `max_price`（见 `specs/SPEC-PRICE-001.md`）。

## 必须保持的原有行为
- `category` 精确匹配继续有效（`test_category_filter`）

## 上下文必须包含的来源
- 规格：`specs/SPEC-PRICE-001.md`
- 代码：`sample_app/catalog.py`
- 测试：`sample_app/tests/test_catalog.py`

## 初始数据
`demo_catalog()` 内置 5 条商品（见源码）。

## 操作步骤
1. 准备 seekdb（或兼容 MySQL 的 OceanBase）与 PowerContext，配置 `.env`
2. `pip install -r requirements.txt`
3. **改前**：`python -m pytest sample_app/tests/test_catalog.py -v` → 价格用例 FAIL
4. `python -m sdd_context.cli run-sample`（需 `MINIMAX_API_KEY`）
5. **改后**：同组测试全部 PASS
6. `python -m sdd_context.cli resume --task-id price-001` 确认状态可复用
7. `python -m sdd_context.cli update-spec-demo` 确认规格更新后上下文跟随

## 预期结果
- 六项必做均有 evidence 证据
- `evidence/catalog.diff` 反映 MiniMax 修改
- 不提交真实 API Key

## 重置方法
```bash
python -c "from sdd_context.module import reset_sample_to_before; reset_sample_to_before()"
# 或从 evidence/before/catalog.py 拷回
```
