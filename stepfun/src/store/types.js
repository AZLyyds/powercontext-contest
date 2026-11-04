/**
 * 向量库抽象层契约
 *
 * 前端只调用 upsert / hybridSearch，不在 UI 层分别跑全文与向量再拼合结果。
 *
 * @typedef {object} DocRecord
 * @property {string} id
 * @property {string} title
 * @property {string} text
 * @property {string[]} [tags]
 * @property {Record<string, unknown>} [metadata]
 *
 * @typedef {object} UpsertItem
 * @property {string} id
 * @property {string} document
 * @property {number[]} embedding
 * @property {Record<string, unknown>} [metadata]
 *
 * @typedef {object} HybridHit
 * @property {string} id
 * @property {string} document
 * @property {number} [score]
 * @property {Record<string, unknown>} [metadata]
 *
 * @typedef {object} VectorStore
 * @property {string} name               后端显示名
 * @property {string} kind               "demo-hybrid" | "seekdb" | ...
 * @property {() => Promise<void>} init
 * @property {(items: UpsertItem[]) => Promise<{ upserted: number }>} upsert
 * @property {(args: { queryText: string, queryEmbedding: number[], topK?: number }) => Promise<HybridHit[]>} hybridSearch
 * @property {() => Promise<number>} count
 */

export {};
