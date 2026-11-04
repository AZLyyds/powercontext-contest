import { initEmbedder, embedTexts, getEmbedderInfo } from "./embedder.js";
import { createStore } from "./store/index.js";

const el = {
  backend: document.getElementById("backend-label"),
  device: document.getElementById("device-label"),
  model: document.getElementById("model-label"),
  count: document.getElementById("doc-count"),
  log: document.getElementById("log"),
  docs: document.getElementById("docs-input"),
  query: document.getElementById("query-input"),
  results: document.getElementById("results"),
  btnLoad: document.getElementById("btn-load-sample"),
  btnImport: document.getElementById("btn-import"),
  btnSearch: document.getElementById("btn-search"),
};

/** @type {import("./store/types.js").VectorStore | null} */
let store = null;

function log(msg) {
  const line = `[${new Date().toLocaleTimeString()}] ${msg}`;
  el.log.textContent = `${el.log.textContent}${line}\n`;
  el.log.scrollTop = el.log.scrollHeight;
  console.log(line);
}

async function refreshCount() {
  if (!store) return;
  el.count.textContent = String(await store.count());
}

async function bootstrap() {
  try {
    store = createStore();
    await store.init();
    el.backend.textContent = `${store.name} [${store.kind}]`;
    log(`后端：${store.name}`);

    const info = await initEmbedder(log);
    el.device.textContent = info.device;
    el.model.textContent = `${info.model}${info.dtype ? ` (${info.dtype})` : ""}`;
    await refreshCount();
  } catch (err) {
    log(`初始化失败：${err?.message || err}`);
    el.device.textContent = "error";
  }
}

el.btnLoad.addEventListener("click", async () => {
  try {
    const mod = await import("../data/sample_docs.json");
    const docs = mod.default ?? mod;
    el.docs.value = JSON.stringify(docs, null, 2);
    log(`已加载示例语料 ${docs.length} 条`);
  } catch (err) {
    log(`加载示例失败：${err?.message || err}`);
  }
});

el.btnImport.addEventListener("click", async () => {
  if (!store) return;
  el.btnImport.disabled = true;
  try {
    if (!getEmbedderInfo().ready) {
      await initEmbedder(log);
      const info = getEmbedderInfo();
      el.device.textContent = info.device;
    }

    const docs = JSON.parse(el.docs.value || "[]");
    if (!Array.isArray(docs) || docs.length === 0) {
      throw new Error("语料为空或不是 JSON 数组");
    }

    log(`开始本地向量化 ${docs.length} 篇…`);
    const texts = docs.map((d) => {
      const title = d.title || "";
      const body = d.text || d.content || "";
      return `${title}\n${body}`.trim();
    });
    const embeddings = await embedTexts(texts);

    const items = docs.map((d, i) => ({
      id: String(d.id || `doc-${i + 1}`),
      document: texts[i],
      embedding: embeddings[i],
      metadata: {
        title: d.title || "",
        tags: d.tags || [],
        source: d.source || "sample",
      },
    }));

    const result = await store.upsert(items);
    log(`写入完成：upserted=${result.upserted} · backend=${store.kind}`);
    await refreshCount();
  } catch (err) {
    log(`导入失败：${err?.message || err}`);
  } finally {
    el.btnImport.disabled = false;
  }
});

el.btnSearch.addEventListener("click", async () => {
  if (!store) return;
  const q = el.query.value.trim();
  if (!q) return;

  el.btnSearch.disabled = true;
  el.results.innerHTML = "";
  try {
    if (!getEmbedderInfo().ready) {
      await initEmbedder(log);
    }
    log(`本地向量化查询：${q}`);
    const [queryEmbedding] = await embedTexts(q);

    // 单一混合检索入口 —— 不做前端两路拼合
    const hits = await store.hybridSearch({
      queryText: q,
      queryEmbedding,
      topK: 5,
    });

    log(`命中 ${hits.length} 条 · backend=${store.kind}`);
    if (!hits.length) {
      el.results.innerHTML = `<p class="hint">无结果</p>`;
      return;
    }

    el.results.innerHTML = hits
      .map((h, idx) => {
        const title = h.metadata?.title || h.id;
        const backend = h.metadata?.backend || store.kind;
        return `
          <article class="result-card">
            <h3>#${idx + 1} ${escapeHtml(String(title))}</h3>
            <div class="meta">
              <span class="badge">id=${escapeHtml(h.id)}</span>
              <span class="badge">score=${Number(h.score ?? 0).toFixed(4)}</span>
              <span class="badge">backend=${escapeHtml(String(backend))}</span>
            </div>
            <p>${escapeHtml(h.document).slice(0, 420)}</p>
          </article>
        `;
      })
      .join("");
  } catch (err) {
    log(`检索失败：${err?.message || err}`);
    el.results.innerHTML = `<p class="error">${escapeHtml(err?.message || String(err))}</p>`;
  } finally {
    el.btnSearch.disabled = false;
  }
});

function escapeHtml(s) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

bootstrap();
