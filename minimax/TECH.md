# 一页技术说明 — MiniMax SDD 上下文模块

## 模块结构
- `sdd_context/`：上下文模块（ingest / context / MiniMax patch / PowerContext 状态）
- `sample_app/`：可运行业务样例（商品筛选）
- `specs/SPEC-PRICE-001.md`：规格
- `evidence/`：修改前后代码、pytest 日志、DB 查询记录、MiniMax 开发记录、PC 读写记录

## 上下文选择方式
1. 将规格 / 代码 / 测试写入 **seekdb（MySQL 协议）** `artifacts` 表（FULLTEXT）
2. 按任务关键词检索，强制附带验证样例必需来源：`SPEC-PRICE-001`、`catalog.py`、`test_catalog.py`
3. 组装上下文交给 **MiniMax**，生成完整文件补丁并落盘
4. 用 **PowerContext**（`POST /v1/sources/content`）保存任务状态；新进程 `resume` 读取复用

## 技术分工
| 技术 | 作用 |
|------|------|
| MiniMax | 根据上下文实际改写 `catalog.py`（见 `evidence/minimax_dev_record.json`） |
| seekdb / OceanBase | 存工程资料并检索，结果进入上下文（见 `evidence/context_bundle.json`） |
| PowerContext | 保存/重启后复用任务状态（见 `evidence/powercontext_*.json`） |

## 验证样例一句话
实现 SPEC-PRICE-001：为 `filter_products` 补上 inclusive `min_price`/`max_price`；改前价格相关测试失败，改后全绿，分类筛选回归仍通过。
