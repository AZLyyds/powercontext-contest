# TECH · 端侧 Embedding + SeekDB 混合检索（一页）

## 目标

浏览器本地完成文档 / 查询向量化（无云端推理 API），向量与正文经 sidecar 写入 SeekDB，**库内** `hybridSearch`（或 sidecar `memory` 单入口融合）完成关键词 + 语义召回。

## 技术选型

| 层 | 选型 | 备注 |
|----|------|------|
| 前端 | Vite + 原生 JS | 默认 `VITE_STORE=seekdb` |
| Embedding | `@huggingface/transformers` + `Xenova/all-MiniLM-L6-v2` | WebGPU → WASM；dtype FP16 / q8 |
| 代理 | `sidecar/`（Node 原生 http） | `/upsert` `/hybrid-search` 等 |
| 向量库 | npm `seekdb`（seekdb-js） | `STORE_BACKEND=seekdb` |
| 无真库演示 | sidecar `STORE_BACKEND=memory` | **同一函数内**向量+全文+RRF，不在浏览器拼合 |

## 数据流

```
导入 JSON → 本地 pipeline(feature-extraction)
         → POST sidecar /upsert
         → 查询本地 embedding
         → POST sidecar /hybrid-search  （唯一检索入口）
         → UI 展示命中 + backend 标签
```

## 量化体积 / 质量对比（占位 · 提交前按复现命令实测）

| 配置 | 权重体积（约） | 设备 | 单条 embedding 时延 | Recall@5（自建集） | 备注 |
|------|----------------|------|---------------------|-------------------|------|
| FP16 | _占位：填写 MB（见下方复现）_ | WebGPU（若可用） | _占位 ms_ | _占位_ | 精度优先；改 embedder dtype |
| INT8 / q8 | _占位：填写 MB_ | WASM | _占位 ms_ | _占位_ | 体积 / 兼容优先（默认倾向） |
| FP32（对照） | _占位：填写 MB_ | WASM | _占位 ms_ | _占位_ | 可选基线 |

**占位说明：** 上表数字依赖本机缓存与硬件，评测机不可照抄他人结果。提交前请在目标环境跑通下方命令，把 Network / 控制台日志里的体积与时延填入；Recall@5 用 `data/sample_docs.json` + 自拟 query 集人工标注。

### 复现命令

```powershell
cd stepfun
npm install
npm --prefix sidecar install
npm run sidecar          # 终端 1，默认 memory；真库加 $env:STORE_BACKEND="seekdb"
npm run dev              # 终端 2

# 健康检查
Invoke-RestMethod http://127.0.0.1:8787/health

# UI：加载示例 → 导入 → 检索「混合检索」
# 记录：
# 1) 首次模型下载体积（浏览器 Network → Xenova/all-MiniLM-L6-v2 onnx）
# 2) 冷/热启动到 embedder ready 的日志时延
# 3) 6 条导入总时延、单条 query embedding 时延
# 4) 结果卡片 backend=memory|seekdb，确认非前端拼合
```

切换 dtype：改 `src/embedder.js` 中 `dtype`（`fp16` / `q8` / `fp32`）后硬刷新页面重测。

## 关键合规点

1. **禁止云端推理 API**（不用 OpenAI / DashScope embedding 等）。
2. **禁止客户端拼合两路检索结果**；UI 只调一次 `hybridSearch`。
3. SeekDB 正式路径：`embeddingFunction: null` + 浏览器向量 + `collection.hybridSearch`。
4. `STORE_BACKEND=memory` 仅演示；**正式提交需 `STORE_BACKEND=seekdb` + 真实 SEEKDB_***。

## SeekDB sidecar 状态

- [x] `sidecar/`：`/health` `/upsert` `/hybrid-search` `/count`
- [x] 默认前端 `VITE_STORE=seekdb` + `VITE_SEEKDB_PROXY`
- [x] `memory` 后端保证无真库时可演示单入口 hybridSearch
- [ ] 评测机补齐上表实测数据
- [ ] 评测机验证 `STORE_BACKEND=seekdb` 连真库

文档：

- https://www.oceanbase.ai/docs/seekdb-js-get-started
- https://github.com/oceanbase/seekdb-js
- https://huggingface.co/docs/transformers.js
