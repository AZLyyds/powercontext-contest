/**
 * 浏览器本地 Embedding（禁止云端推理 API）
 * 模型：Xenova/all-MiniLM-L6-v2（384 维）
 * 设备：优先 WebGPU，失败降级 WASM
 *
 * 依赖：@huggingface/transformers（原 @xenova/transformers）
 */

const MODEL_ID = "Xenova/all-MiniLM-L6-v2";

/** @type {import("@huggingface/transformers").FeatureExtractionPipeline | null} */
let extractor = null;
/** @type {"webgpu" | "wasm" | "unknown"} */
let activeDevice = "unknown";

/**
 * @param {(msg: string) => void} [log]
 */
export async function initEmbedder(log = () => {}) {
  if (extractor) {
    return { device: activeDevice, model: MODEL_ID };
  }

  const { pipeline, env } = await import("@huggingface/transformers");

  // 允许浏览器缓存模型；不走远端推理 API
  env.allowLocalModels = false;
  env.useBrowserCache = true;

  const tries = [
    { device: "webgpu", dtype: "fp16" },
    { device: "wasm", dtype: "q8" }, // INT8 / 量化路径
  ];

  let lastError = null;
  for (const opt of tries) {
    try {
      log(`尝试加载模型 ${MODEL_ID} · device=${opt.device} dtype=${opt.dtype}`);
      extractor = await pipeline("feature-extraction", MODEL_ID, {
        device: opt.device,
        dtype: opt.dtype,
      });
      activeDevice = /** @type {"webgpu"|"wasm"} */ (opt.device);
      log(`模型就绪：${activeDevice} / ${opt.dtype}`);
      return { device: activeDevice, model: MODEL_ID, dtype: opt.dtype };
    } catch (err) {
      lastError = err;
      log(`失败 (${opt.device}): ${err?.message || err}`);
      extractor = null;
    }
  }

  throw lastError || new Error("无法初始化本地 embedding 管线");
}

export function getEmbedderInfo() {
  return { device: activeDevice, model: MODEL_ID, ready: Boolean(extractor) };
}

/**
 * @param {string | string[]} texts
 * @returns {Promise<number[][]>}
 */
export async function embedTexts(texts) {
  if (!extractor) {
    throw new Error("Embedder 未初始化，请先调用 initEmbedder()");
  }
  const list = Array.isArray(texts) ? texts : [texts];
  const vectors = [];

  for (const text of list) {
    const output = await extractor(text, {
      pooling: "mean",
      normalize: true,
    });
    // Transformers.js 返回 Tensor；转普通数组
    const data = Array.from(output.data ?? output);
    vectors.push(data);
  }

  return vectors;
}
