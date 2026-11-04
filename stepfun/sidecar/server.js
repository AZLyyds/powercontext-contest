/**
 * SeekDB sidecar · HTTP 代理
 *
 * 端点：
 *   GET  /health
 *   POST /upsert          { items: [{id, document, embedding, metadata}] }
 *   POST /hybrid-search   { queryText, queryEmbedding, topK }
 *   GET  /count
 *
 * 环境变量：
 *   PORT=8787
 *   STORE_BACKEND=memory|seekdb   （默认 memory；正式提交用 seekdb）
 *   SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASSWORD / SEEKDB_DATABASE
 *   SEEKDB_PATH                   （Embedded 模式，设置后优先于 host）
 *   SEEKDB_COLLECTION=mini_kb
 *   SEEKDB_DIMENSION=384
 *   SEEKDB_TENANT                 （连 OceanBase 时可选）
 */

import http from "node:http";
import { createMemoryBackend } from "./memoryBackend.js";

const PORT = Number(process.env.PORT || 8787);
const HOST = process.env.HOST || "127.0.0.1";

/** @type {Awaited<ReturnType<typeof createMemoryBackend>> | null} */
let store = null;
let bootError = null;

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

function sendJson(res, status, body) {
  cors(res);
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8");
        resolve(raw ? JSON.parse(raw) : {});
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

async function initStore() {
  const backend = (process.env.STORE_BACKEND || "memory").toLowerCase();

  if (backend === "seekdb") {
    const { createSeekdbBackend } = await import("./seekdbBackend.js");
    store = await createSeekdbBackend();
    console.log(`[sidecar] backend=seekdb · ${store.name}`);
    return;
  }

  store = createMemoryBackend();
  console.log(
    `[sidecar] backend=memory · 库内混合检索模拟（正式提交请设 STORE_BACKEND=seekdb 并配置 SEEKDB_*）`,
  );
}

const server = http.createServer(async (req, res) => {
  cors(res);

  if (req.method === "OPTIONS") {
    res.writeHead(204);
    res.end();
    return;
  }

  const url = new URL(req.url || "/", `http://${HOST}:${PORT}`);
  const path = url.pathname;

  try {
    if (req.method === "GET" && path === "/health") {
      sendJson(res, 200, {
        ok: !bootError,
        backend: store?.kind || null,
        name: store?.name || null,
        error: bootError ? String(bootError.message || bootError) : null,
      });
      return;
    }

    if (!store) {
      sendJson(res, 503, {
        error: bootError
          ? `store 初始化失败: ${bootError.message || bootError}`
          : "store 未就绪",
      });
      return;
    }

    if (req.method === "GET" && path === "/count") {
      const count = await store.count();
      sendJson(res, 200, { count, backend: store.kind });
      return;
    }

    if (req.method === "POST" && path === "/upsert") {
      const body = await readJson(req);
      const items = body.items;
      if (!Array.isArray(items)) {
        sendJson(res, 400, { error: "body.items 必须是数组" });
        return;
      }
      const result = await store.upsert(items);
      sendJson(res, 200, result);
      return;
    }

    if (req.method === "POST" && path === "/hybrid-search") {
      const body = await readJson(req);
      const { queryText, queryEmbedding, topK } = body;
      if (!Array.isArray(queryEmbedding)) {
        sendJson(res, 400, { error: "queryEmbedding 必须是 number[]" });
        return;
      }
      const result = await store.hybridSearch({
        queryText: queryText ?? "",
        queryEmbedding,
        topK: Number(topK) || 5,
      });
      sendJson(res, 200, result);
      return;
    }

    sendJson(res, 404, { error: `not found: ${req.method} ${path}` });
  } catch (err) {
    console.error("[sidecar]", err);
    sendJson(res, 500, { error: String(err?.message || err) });
  }
});

await initStore().catch((err) => {
  bootError = err;
  console.error("[sidecar] 初始化失败，将以 503 响应业务接口：", err);
  // memory 兜底：seekdb 连不上时仍可演示
  if ((process.env.STORE_BACKEND || "memory").toLowerCase() === "seekdb") {
    console.warn("[sidecar] 回退到 STORE_BACKEND=memory");
    store = createMemoryBackend();
    bootError = null;
  }
});

server.listen(PORT, HOST, () => {
  console.log(`[sidecar] http://${HOST}:${PORT}`);
  console.log(
    `[sidecar] health → GET /health · upsert → POST /upsert · search → POST /hybrid-search`,
  );
});
