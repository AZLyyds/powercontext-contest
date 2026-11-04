# StepFun 赛题骨架 · 端侧 Embedding + SeekDB 混合检索迷你知识库

浏览器本地用 Transformers.js / ONNX 做 embedding（INT8 或 FP16），WebGPU 优先并降级 WASM；经 Node sidecar 写入 SeekDB（npm：`seekdb`）；**混合检索在 sidecar / 库内单入口完成**，浏览器禁止两路拼合。

## 快速开始（推荐）

**Windows 一键：**

```powershell
cd stepfun
.\start.ps1
```

脚本会：安装前后端依赖 → 起 sidecar（默认 `STORE_BACKEND=memory`）→ 起 Vite。  
正式连真库：`.\start.ps1 -StoreBackend seekdb`（需本机 SeekDB / OceanBase 已就绪并配置 `SEEKDB_*`）。

**手动两步：**

```powershell
cd stepfun
npm install
npm --prefix sidecar install   # sidecar 已配 .npmrc: legacy-peer-deps=true

# 终端 1：先起代理（默认 memory 混合检索，无需真库也能演示）
npm run sidecar

# 终端 2：再起前端（.env 默认 VITE_STORE=seekdb）
npm run dev
```

打开 Vite 提示的地址（默认 `http://127.0.0.1:5173`）：

1. **加载示例语料** → **导入并向量化写入**
2. 输入查询（如「混合检索」）→ **检索**
3. 结果卡片 `backend=` 应为 `memory`（sidecar 模拟）或 `seekdb`（真库）

> 首次加载模型需要下载权重，请保持网络畅通。  
> **正式提交必须使用真实 SeekDB**：见下方「切换真实 SeekDB」。

## 环境变量

复制 `.env.example` → `.env`（仓库已提供默认值）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_STORE` | `seekdb` | `seekdb`=走代理；`demo`=浏览器 IndexedDB |
| `VITE_SEEKDB_PROXY` | `http://127.0.0.1:8787` | sidecar 地址 |

Sidecar（进程环境，非 Vite）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `STORE_BACKEND` | `memory` | `memory`=库内混合模拟；`seekdb`=真库 |
| `PORT` | `8787` | 代理端口 |
| `SEEKDB_HOST` / `PORT` / `USER` / `PASSWORD` / `DATABASE` | `127.0.0.1` / `2881` / `root` / `` / `kb` | Server 模式 |
| `SEEKDB_PATH` | — | 设置则用 Embedded（如 `./seekdb.db`） |
| `SEEKDB_COLLECTION` | `mini_kb` | 集合名 |
| `SEEKDB_DIMENSION` | `384` | 与 MiniLM 一致 |

## 切换真实 SeekDB（正式提交）

1. 按 [官方文档](https://www.oceanbase.ai/docs/seekdb-js-get-started) 部署 SeekDB / OceanBase。
2. 安装 sidecar 依赖（若 `npm install seekdb` 失败，可用 memory 演示，但**不可作为正式提交后端**）：

```powershell
cd sidecar
npm install
```

3. 启动真库代理：

```powershell
$env:STORE_BACKEND="seekdb"
$env:SEEKDB_HOST="127.0.0.1"
$env:SEEKDB_PORT="2881"
$env:SEEKDB_USER="root"
$env:SEEKDB_PASSWORD=""
$env:SEEKDB_DATABASE="kb"
npm run sidecar
```

4. 前端保持 `.env` 中 `VITE_STORE=seekdb`，再 `npm run dev`。

## 目录

| 路径 | 说明 |
|------|------|
| `index.html` / `src/` | 最小前端 |
| `sidecar/` | SeekDB HTTP 代理（`/health` `/upsert` `/hybrid-search` `/count`） |
| `data/sample_docs.json` | 示例语料 |
| `SCHEMA.md` | 集合设计（与 sidecar 一致） |
| `TECH.md` | 技术说明 + 量化对比表占位与复现命令 |
| `start.ps1` | Windows 一键启动 |
| `.env.example` | 前端默认 seekdb 代理 |

## 约束对照

| 要求 | 本骨架做法 |
|------|------------|
| 端侧 embedding | `src/embedder.js`，模型 `Xenova/all-MiniLM-L6-v2` |
| 禁止云端推理 | 不调用任何 embedding HTTP API |
| 库内混合检索 | UI 只调 `store.hybridSearch`；sidecar 内 `collection.hybridSearch` 或 memory 单函数融合 |
| 可提交路径 | `VITE_STORE=seekdb` + sidecar；正式评测 `STORE_BACKEND=seekdb` |

## 许可证 / 提交

按大赛要求打包 `stepfun/` 目录；补齐 `TECH.md` 实测表后再提交。
