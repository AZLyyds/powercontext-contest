/**
 * 库内混合检索模拟：同一函数内同时做向量相似度 + 全文，再 RRF 融合。
 * 用于本机无法连真实 SeekDB 时演示；正式提交请切 STORE_BACKEND=seekdb。
 */

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
  const n = Math.min(a?.length || 0, b?.length || 0);
  for (let i = 0; i < n; i++) {
    dot += a[i] * b[i];
    na += a[i] * a[i];
    nb += b[i] * b[i];
  }
  if (!na || !nb) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

function rrfFuse(rankLists, k = 60) {
  const scores = new Map();
  for (const list of rankLists) {
    list.forEach((id, idx) => {
      scores.set(id, (scores.get(id) || 0) + 1 / (k + idx + 1));
    });
  }
  return scores;
}

export function createMemoryBackend() {
  /** @type {Map<string, {id:string, document:string, embedding:number[], metadata:object}>} */
  const docs = new Map();

  return {
    kind: "memory",
    name: "memory hybrid (sidecar 内融合)",

    async upsert(items) {
      for (const item of items) {
        docs.set(String(item.id), {
          id: String(item.id),
          document: String(item.document ?? ""),
          embedding: Array.isArray(item.embedding) ? item.embedding : [],
          metadata: item.metadata && typeof item.metadata === "object" ? item.metadata : {},
        });
      }
      return { upserted: items.length, backend: "memory" };
    },

    async hybridSearch({ queryText, queryEmbedding, topK = 5 }) {
      const all = [...docs.values()];
      const qTokens = new Set(tokenize(queryText || ""));

      const lexical = all
        .map((doc) => {
          const tokens = tokenize(doc.document);
          let hit = 0;
          for (const t of tokens) if (qTokens.has(t)) hit += 1;
          const titleBoost = String(doc.metadata?.title || "")
            .toLowerCase()
            .includes(String(queryText || "").toLowerCase())
            ? 2
            : 0;
          return { id: doc.id, score: hit + titleBoost };
        })
        .filter((x) => x.score > 0)
        .sort((a, b) => b.score - a.score);

      const semantic = all
        .map((doc) => ({
          id: doc.id,
          score: cosine(queryEmbedding || [], doc.embedding || []),
        }))
        .sort((a, b) => b.score - a.score);

      // 单入口内融合 —— 不把两路结果返回给浏览器拼合
      const fused = rrfFuse(
        [lexical.map((x) => x.id), semantic.map((x) => x.id)],
        60,
      );

      const byId = new Map(all.map((d) => [d.id, d]));
      const hits = [...fused.entries()]
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
              backend: "memory",
              lexicalRank: lexical.findIndex((x) => x.id === id),
              semanticRank: semantic.findIndex((x) => x.id === id),
            },
          };
        });

      return { hits, backend: "memory" };
    },

    async count() {
      return docs.size;
    },
  };
}
