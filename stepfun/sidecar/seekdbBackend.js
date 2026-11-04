/**
 * 真实 SeekDB 后端（npm: seekdb / seekdb-js）
 * 文档：https://www.oceanbase.ai/docs/seekdb-js-get-started
 *       https://github.com/oceanbase/seekdb-js
 */

const COLLECTION = process.env.SEEKDB_COLLECTION || "mini_kb";
const DIMENSION = Number(process.env.SEEKDB_DIMENSION || 384);

/** @returns {Promise<{kind:string,name:string,upsert:Function,hybridSearch:Function,count:Function}>} */
export async function createSeekdbBackend() {
  let seekdb;
  try {
    seekdb = await import("seekdb");
  } catch (err) {
    throw new Error(
      `无法加载 npm 包 seekdb：${err?.message || err}。` +
        `请在 sidecar/ 执行 npm install（需 legacy-peer-deps），或改用 STORE_BACKEND=memory。`,
    );
  }

  const {
    SeekdbClient,
    Schema,
    FulltextIndexConfig,
    VectorIndexConfig,
  } = seekdb;

  const path = process.env.SEEKDB_PATH || "";
  const clientArgs = path
    ? {
        path,
        database: process.env.SEEKDB_DATABASE || "kb",
      }
    : {
        host: process.env.SEEKDB_HOST || "127.0.0.1",
        port: Number(process.env.SEEKDB_PORT || 2881),
        user: process.env.SEEKDB_USER || "root",
        password: process.env.SEEKDB_PASSWORD || "",
        database: process.env.SEEKDB_DATABASE || "kb",
        ...(process.env.SEEKDB_TENANT
          ? { tenant: process.env.SEEKDB_TENANT }
          : {}),
      };

  const client = new SeekdbClient(clientArgs);

  const schema = new Schema({
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension: DIMENSION, distance: "cosine" },
      // 浏览器已算好向量，禁用服务端自动 embedding
      embeddingFunction: null,
    }),
  });

  let collection;
  try {
    collection = await client.getOrCreateCollection({
      name: COLLECTION,
      schema,
    });
  } catch (err) {
    // 部分版本 getOrCreate 对已有集合 schema 敏感：先 get，失败再 create
    try {
      collection = await client.getCollection({ name: COLLECTION });
    } catch {
      collection = await client.createCollection({ name: COLLECTION, schema });
    }
    if (!collection) throw err;
  }

  return {
    kind: "seekdb",
    name: path
      ? `seekdb embedded (${path}/${COLLECTION})`
      : `seekdb server (${clientArgs.host}:${clientArgs.port}/${COLLECTION})`,

    async upsert(items) {
      if (!items?.length) return { upserted: 0, backend: "seekdb" };

      // 元数据中的数组等需可 JSON 序列化；tags 转字符串避免驱动挑剔
      const metadatas = items.map((x) => {
        const m = { ...(x.metadata || {}) };
        if (Array.isArray(m.tags)) m.tags = m.tags.join(",");
        return m;
      });

      await collection.upsert({
        ids: items.map((x) => String(x.id)),
        documents: items.map((x) => String(x.document ?? "")),
        embeddings: items.map((x) => x.embedding),
        metadatas,
      });

      // 尽快可搜（若 SDK 提供 refresh）
      if (typeof collection.refresh === "function") {
        try {
          await collection.refresh();
        } catch {
          /* optional */
        }
      }

      return { upserted: items.length, backend: "seekdb" };
    },

    async hybridSearch({ queryText, queryEmbedding, topK = 5 }) {
      const result = await collection.hybridSearch({
        query: {
          whereDocument: { $contains: String(queryText || "") },
        },
        knn: {
          queryEmbeddings: [queryEmbedding],
        },
        nResults: topK,
        rank: { rrf: { rankConstant: 60 } },
        include: ["documents", "metadatas", "distances"],
      });

      // QueryResult: ids/documents/metadatas/distances 均为「按 query 分组」的二维数组
      const ids = result?.ids?.[0] || [];
      const documents = result?.documents?.[0] || [];
      const metadatas = result?.metadatas?.[0] || [];
      const distances = result?.distances?.[0] || [];

      const hits = ids.map((id, i) => {
        const dist = distances[i];
        const score =
          typeof dist === "number" ? 1 / (1 + Math.max(0, dist)) : 0;
        return {
          id: String(id),
          document: documents[i] ?? "",
          score,
          metadata: {
            ...(metadatas[i] || {}),
            backend: "seekdb",
            distance: dist,
          },
        };
      });

      return { hits, backend: "seekdb" };
    },

    async count() {
      if (typeof collection.count === "function") {
        return await collection.count();
      }
      return 0;
    },
  };
}
