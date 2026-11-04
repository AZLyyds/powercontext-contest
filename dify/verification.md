# 记仇 NPC · 验证样例

以下可先对 **backend** 单独验证，再在 Dify 调试台复现同一会话。

假设：

- Backend：`http://127.0.0.1:8765`
- 会话 ID：`demo-session-001`
- 默认模式：`POWERCONTEXT_MODE=file`（本地 JSON）

---

## 0. 健康检查 / 自测脚本

```bash
# 进程内自测（无需先起服务）
cd dify/backend
python smoke_test.py

# 或对已启动服务
curl -s http://127.0.0.1:8765/health
python smoke_test.py --live http://127.0.0.1:8765
```

期望 `/health`：

```json
{"ok": true, "mode": "file", "backend": "json", "json_path": "..."}
```

OpenAPI：<http://127.0.0.1:8765/docs>

---

## 1. 记下一笔仇

```bash
curl -s -X POST http://127.0.0.1:8765/memory/save ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":\"demo-session-001\",\"event\":\"玩家把NPC的药水藏起来了（恶作剧）\",\"severity\":4,\"tags\":[\"prank\",\"potion\"]}"
```

期望：`ok=true`，返回带 `id` / `created_at` 的 `record`。

---

## 2. 再记一笔

```bash
curl -s -X POST http://127.0.0.1:8765/memory/save ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":\"demo-session-001\",\"event\":\"玩家在门口挖坑整蛊NPC\",\"severity\":3,\"tags\":[\"prank\",\"trap\"]}"
```

---

## 3. 翻旧账（按 query）

```bash
curl -s -X POST http://127.0.0.1:8765/memory/recall ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":\"demo-session-001\",\"query\":\"药水\",\"limit\":5}"
```

期望：`grudges` 中优先出现「药水」相关事件。

---

## 4. 无关键词召回（最近 N 条）

```bash
curl -s -X POST http://127.0.0.1:8765/memory/recall ^
  -H "Content-Type: application/json" ^
  -d "{\"session_id\":\"demo-session-001\",\"query\":\"\",\"limit\":5}"
```

期望：返回该 session 最近记录（新在前）。

---

## 5. Dify 对话验收剧本（同一会话连续测）

| 轮次 | 用户输入 | 期望行为 |
|------|----------|----------|
| 1 | 我把你的药水藏起来了，恶作剧！ | 走 save；回复承认「记下了」；backend `grudges.json` 新增一条 |
| 2 | 今天天气怎么样？ | 走 recall；回复中提及药水/挖坑等旧账 |
| 3 | 你还记得我吗？ | 继续翻旧账，语气阴阳但娱乐向 |
| 4 | 我又在门口挖坑整蛊你 | 再 save；可同时嘲讽「惯犯」 |

验收点：

1. 轮次 1 / 4 后，`backend/data/grudges.json` 中对应 `conversation_id` 下 `grudges` 增长。
2. 轮次 2 / 3 的 LLM 输出应能指出至少一条历史事件（依赖 prompt 注入 HTTP body）。
3. HTTP 节点状态码均为 200。

---

## 6. PowerContext 模式抽检（可选）

`.env` 设：

```env
POWERCONTEXT_MODE=http
POWERCONTEXT_BASE_URL=http://127.0.0.1:8000
POWERCONTEXT_SCOPE=grudge-npc
```

后重复样例 1～3：

- `/health` 显示 `"mode": "http"`
- 若 `/v1/sources/content` 或 search 404：按部署 OpenAPI 修改 `powercontext_client.py` 后重试
- 成功时 `record` / `grudges` 来自远端，而不再写入本地 JSON

---

## 一键 PowerShell 脚本（可选）

```powershell
$base = "http://127.0.0.1:8765"
$sid = "demo-session-001"
Invoke-RestMethod "$base/health"
Invoke-RestMethod -Method Post "$base/memory/save" -ContentType "application/json" -Body (@{session_id=$sid; event="玩家藏药水恶作剧"; severity=4; tags=@("prank")} | ConvertTo-Json)
Invoke-RestMethod -Method Post "$base/memory/recall" -ContentType "application/json" -Body (@{session_id=$sid; query="药水"; limit=5} | ConvertTo-Json)
```
