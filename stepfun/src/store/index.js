import { createDemoHybridStore } from "./demoHybrid.js";
import { createSeekdbStore } from "./seekdbAdapter.js";

/**
 * 默认走 SeekDB 代理（VITE_STORE=seekdb + VITE_SEEKDB_PROXY）。
 * 设 VITE_STORE=demo 可回退浏览器 IndexedDB 演示后端。
 *
 * @returns {import("./types.js").VectorStore}
 */
export function createStore() {
  const mode =
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_STORE) || "seekdb";

  if (mode === "demo" || mode === "demo-hybrid") {
    return createDemoHybridStore();
  }
  return createSeekdbStore();
}

export { createDemoHybridStore, createSeekdbStore };
