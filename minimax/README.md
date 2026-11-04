# MiniMax × OceanBase — 规有所依（SDD 上下文模块）

目录：`minimax/`（分支 `2026-competition-ecosystem`）

## 依赖版本
- Python 3.10+
- `pymysql`, `pytest`（见 `requirements.txt`）
- seekdb / OceanBase（MySQL 协议，默认 `127.0.0.1:2881`）
- PowerContext HTTP（默认 `http://127.0.0.1:8000`）
- MiniMax API（`MINIMAX_API_KEY`，模型默认 `MiniMax-M2`）

## 配置
```bash
cd minimax
cp .env.example .env   # 填入真实 Key / 地址；勿提交 .env
pip install -r requirements.txt
# 可选：docker compose -f docker-compose.seekdb.yml up -d
# 另起 PowerContext 服务，或先 POWERCONTEXT_MODE=file 本地演练（计分请用真实 PC）
```

Windows PowerShell 临时环境变量示例：
```powershell
$env:MINIMAX_API_KEY="..."
$env:SEEKDB_HOST="127.0.0.1"
$env:SEEKDB_PORT="2881"
$env:POWERCONTEXT_BASE_URL="http://127.0.0.1:8000"
$env:POWERCONTEXT_MODE="http"
```

## 调用入口
```bash
python -m sdd_context.cli ingest
python -m sdd_context.cli context --task "implement SPEC-PRICE-001"
python -m sdd_context.cli run-sample          # 完整验证流水线（需 MiniMax）
python -m sdd_context.cli resume --task-id price-001
python -m sdd_context.cli update-spec-demo    # 规格更新后上下文跟随
```

仅本地联调（**不计 MiniMax 分**）：
```bash
python -m sdd_context.cli run-sample --dry-run-patch
```

## 已知限制
- seekdb 镜像标签因环境而异，连不上时请改用已有 OceanBase/seekdb 实例
- PowerContext 路由随版本可能略有差异；以写入 source + 重启读取为准
- 不要把真实 Key 写入仓库

详见 `VERIFY.md`、`TECH.md`；运行产物落在 `evidence/`。
