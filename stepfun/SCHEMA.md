# 表 / 集合设计说明

## 集合：`mini_kb`

面向迷你知识库的单集合设计，同时启用 **全文索引** 与 **稠密向量索引**，混合检索一律走 sidecar 的 `POST /hybrid-search`（真库时对应 SeekDB `collection.hybridSearch`）。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string (PK) | 文档稳定 ID，如 `kb-001` |
| `document` | text | 检索正文（建议 `title + "\n" + text`） |
| `embedding` | float32[384] | 端侧 MiniLM 向量；写入时由浏览器计算 |
| `metadata.title` | string | 展示标题 |
| `metadata.tags` | string / string[] | 标签；sidecar 写入真库时数组会 `join(",")` |
| `metadata.source` | string | 语料来源 |
| `metadata.model` | string | 建议记录 `Xenova/all-MiniLM-L6-v2` |
| `metadata.dtype` | string | `q8` / `fp16` 等，便于对比实验 |

## Sidecar HTTP 约定（与实现一致）

| 方法 | 路径 | 请求体 | 响应 |
|------|------|--------|------|
| GET | `/health` | — | `{ ok, backend, name }` |
| POST | `/upsert` | `{ items: [{ id, document, embedding, metadata }] }` | `{ upserted, backend }` |
| POST | `/hybrid-search` | `{ queryText, queryEmbedding, topK }` | `{ hits: [{ id, document, score, metadata }], backend }` |
| GET | `/count` | — | `{ count, backend }` |

`backend`：`memory` | `seekdb`。`STORE_BACKEND` 控制；正式提交用 `seekdb`。

## SeekDB Schema（`sidecar/seekdbBackend.js`）

```js
import {
  Schema,
  FulltextIndexConfig,
  VectorIndexConfig,
} from "seekdb";

const schema = new Schema({
  fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
  vectorIndex: new VectorIndexConfig({
    hnsw: { dimension: 384, distance: "cosine" },
    embeddingFunction: null, // 禁用服务端自动向量化；使用浏览器传入的 embeddings
  }),
});

await client.getOrCreateCollection({ name: "mini_kb", schema });
```

## 写入

```js
await collection.upsert({
  ids,
  documents,
  embeddings, // number[][]，384 维
  metadatas,
});
```

## 检索（唯一入口）

```js
await collection.hybridSearch({
  query: { whereDocument: { $contains: queryText } },
  knn: { queryEmbeddings: [queryEmbedding] },
  nResults: topK,
  rank: { rrf: { rankConstant: 60 } },
  include: ["documents", "metadatas", "distances"],
});
```

> 禁止：前端分别调用全文 API 与向量 API 后自行 merge/RRF。  
> `STORE_BACKEND=memory` 在 sidecar **同一函数**内做全文+向量+RRF，仅用于无真库演示；正式评测请 `STORE_BACKEND=seekdb`。

## 索引与距离

- 向量维度：384（all-MiniLM-L6-v2）
- 距离：cosine（与 `normalize: true` 的 embedding 一致）
- 全文分析器：中文场景优先 `ik`；以官方文档为准

## 参考

- https://www.oceanbase.ai/docs/seekdb-js-get-started
- https://github.com/oceanbase/seekdb-js
- https://www.npmjs.com/package/seekdb
