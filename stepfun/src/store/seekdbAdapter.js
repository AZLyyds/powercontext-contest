/**
 * SeekDB（seekdb-js）适配层
 *
 * 官方资料：
 * - npm 包名：`seekdb`（仓库仍叫 seekdb-js）
 *   https://www.npmjs.com/package/seekdb
 * - GitHub：https://github.com/oceanbase/seekdb-js
 * - 文档：https://www.oceanbase.ai/docs/seekdb-js-get-started
 * - 部署：https://www.oceanbase.ai/docs/deploy-overview/
 *
 * 重要限制：
 * - 当前 SDK 依赖 Node（mysql2 / native Embedded bindings），**不能直接在浏览器 bundle**。
 * - 推荐架构：浏览器本地 Embedding → HTTP 调用本机 Node sidecar → sidecar 内
 *   `collection.add({ embeddings })` + `collection.hybridSearch(...)`。
 *
 * 本文件提供浏览器侧代理客户端：请求 `sidecar/`（默认 http://127.0.0.1:8787）。
 * 真实 SeekDB / memory 混合检索均在 sidecar 内完成，浏览器只调一次 hybridSearch。
 *
 * 实现见：`sidecar/server.js`、`sidecar/seekdbBackend.js`、`sidecar/memoryBackend.js`
 */

/**
 * @returns {import("./types.js").VectorStore}
 */
export function createSeekdbStore(options = {}) {
  const proxyBase = (
    options.proxyBase ||
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_SEEKDB_PROXY) ||
    "http://127.0.0.1:8787"
  ).replace(/\/$/, "");

  return {
    name: `SeekDB via proxy (${proxyBase})`,
    kind: "seekdb",

    async init() {
      try {
        const res = await fetch(`${proxyBase}/health`);
        if (!res.ok) {
          console.warn(`[seekdb] proxy health HTTP ${res.status} · 请先 npm run sidecar`);
        }
      } catch {
        console.warn(
          `[seekdb] 无法连接 ${proxyBase} · 请先在另一终端运行 npm run sidecar`,
        );
      }
    },

    async upsert(items) {
      const res = await fetch(`${proxyBase}/upsert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items }),
      });
      if (!res.ok) {
        const errBody = await res.text().catch(() => "");
        throw new Error(
          `seekdb proxy upsert failed: ${res.status} ${errBody}`.trim() +
            ` · 确认 sidecar 已启动（npm run sidecar）`,
        );
      }
      return res.json();
    },

    async hybridSearch({ queryText, queryEmbedding, topK = 5 }) {
      const res = await fetch(`${proxyBase}/hybrid-search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queryText, queryEmbedding, topK }),
      });
      if (!res.ok) {
        const errBody = await res.text().catch(() => "");
        throw new Error(
          `seekdb proxy hybrid-search failed: ${res.status} ${errBody}`.trim(),
        );
      }
      const data = await res.json();
      return data.hits || [];
    },

    async count() {
      try {
        const res = await fetch(`${proxyBase}/count`);
        if (!res.ok) return 0;
        const data = await res.json();
        return data.count ?? 0;
      } catch {
        return 0;
      }
    },
  };
}

/* 服务端实现已落地：sidecar/seekdbBackend.js（对照官方 hybridSearch API） */
