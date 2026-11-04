/**
 * 演示后端：单进程内完成「全文 + 向量」混合检索与融合。
 *
 * 用途：在未接入真实 seekdb 时，保证 UI 流程可演示。
 * 注意：融合逻辑在 Store 内部，UI 只调用一次 hybridSearch——符合「禁止客户端拼合两路结果」的赛题约束语义。
 * 正式提交请切换到 seekdbAdapter（库内 DBMS_HYBRID_SEARCH / collection.hybridSearch）。
 */

const DB_NAME = "stepfun-demo-kb";
const STORE_NAME = "docs";

/**
 * @returns {Promise<IDBDatabase>}
 */
function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: "id" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/**
 * @template T
 * @param {IDBRequest<T>} req
 * @returns {Promise<T>}
 */
function idbReq(req) {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tokenize(text) {
  return String(text)
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

function cosine(a, b) {
  let dot = 0;
  let na = 0;
  let nb = 0;
  const n = Math.min(a.length, b.length);
  for (let i = 0; i < n; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (!na || !nb) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/** Reciprocal Rank Fusion */
function rrfFuse(rankLists, k = 60) {
  /** @type {Map<string, number>} */
  const scores = new Map();
  for (const list of rankLists) {
    list.forEach((id, idx) => {
      scores.set(id, (scores.get(id) || 0) + 1 / (k + idx + 1));
    });
  }
  return scores;
}

/**
 * @returns {import("./types.js").VectorStore}
 */
export function createDemoHybridStore() {
  /** @type {IDBDatabase | null} */
  let db = null;

  return {
    name: "DemoHybridStore (IndexedDB · 模拟库内混合检索)",
    kind: "demo-hybrid",

    async init() {
      db = await openDb();
    },

    async upsert(items) {
      if (!db) throw new Error("store 未 init");
      const tx = db.transaction(STORE_NAME, "readwrite");
      const store = tx.objectStore(STORE_NAME);
      for (const item of items) {
        store.put({
          id: item.id,
          document: item.document,
          embedding: item.embedding,
          metadata: item.metadata || {},
        });
      }
      await new Promise((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onerror = () => reject(tx.error);
      });
      return { upserted: items.length };
    },

    async hybridSearch({ queryText, queryEmbedding, topK = 5 }) {
      if (!db) throw new Error("store 未 init");
      const tx = db.transaction(STORE_NAME, "readonly");
      const all = /** @type {Array<{id:string,document:string,embedding:number[],metadata:object}>} */ (
        await idbReq(tx.objectStore(STORE_NAME).getAll())
      );

      const qTokens = new Set(tokenize(queryText));

      const lexical = [...all]
        .map((doc) => {
          const tokens = tokenize(doc.document);
          let hit = 0;
          for (const t of tokens) if (qTokens.has(t)) hit += 1;
          const titleBoost = String(doc.metadata?.title || "")
            .toLowerCase()
            .includes(queryText.toLowerCase())
            ? 2
            : 0;
          return { id: doc.id, score: hit + titleBoost };
        })
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score);

      const semantic = [...all]
        .map((doc) => ({
          id: doc.id,
          score: cosine(queryEmbedding, doc.embedding || []),
        }))
        .sort((a, b) => b.score - a.score);

      const fused = rrfFuse(
        [lexical.map((x) => x.id), semantic.map((x) => x.id)],
        60,
      );

      const byId = new Map(all.map((d) => [d.id, d]));
      return [...fused.entries()]
        .sort((a, b) => b[1] - a[1])
        .slice(0, topK)
        .map(([id, score]) => {
          const doc = byId.get(id);
          return {
            id,
            document: doc?.document || "",
            score,
            metadata: {
              ...(doc?.metadata || {}),
              backend: "demo-hybrid",
              lexicalRank: lexical.findIndex((x) => x.id === id),
              semanticRank: semantic.findIndex((x) => x.id === id),
            },
          };
        });
    },

    async count() {
      if (!db) return 0;
      const tx = db.transaction(STORE_NAME, "readonly");
      return /** @type {number} */ (await idbReq(tx.objectStore(STORE_NAME).count()));
    },
  };
}
