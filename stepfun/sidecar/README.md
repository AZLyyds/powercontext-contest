# SeekDB sidecar

Node HTTP 代理：浏览器 Embedding → 本服务 → SeekDB `hybridSearch`（或 memory 模拟）。

```bash
npm install          # 需 .npmrc legacy-peer-deps=true
npm start            # 或在上级目录 npm run sidecar
```

默认 `STORE_BACKEND=memory`。正式提交：

```bash
STORE_BACKEND=seekdb SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 npm start
```
