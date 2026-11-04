# 记仇 NPC（Dify Chatflow × PowerContext）

玩家恶作剧会被记下，之后闲聊可能被 NPC 翻旧账阴阳怪气。

---

## 小白步骤（按顺序做）

### 1. 装依赖并启动 backend

```bash
cd dify/backend
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux

python main.py
```

看到服务起来后，浏览器打开：<http://127.0.0.1:8765/docs>  
或自测：

```bash
python smoke_test.py
```

默认 `POWERCONTEXT_MODE=file`（本地 JSON，**不必**先起 PowerContext）。

### 2. 在 Dify 导入工作流

1. 打开 [Dify](https://dify.ai)（Cloud）或你的自托管工作室（建议 **1.0+ / 0.15+**）。
2. **创建应用 → 导入 DSL** → 选择 `workflows/grudge-npc.yml`。
3. 若导入报错：打开该 YAML **文件头注释**，按「手动搭法」在画布搭同样节点即可。

### 3. 改 HTTP 节点 URL（必做）

打开画布里两个 **HTTP Request** 节点，确认 URL：

| 节点 | Method | URL |
|------|--------|-----|
| 记仇 | POST | `http://127.0.0.1:8765/memory/save` |
| 翻旧账 | POST | `http://127.0.0.1:8765/memory/recall` |

- Dify 跑在 **Docker**、backend 在宿主机：把 `127.0.0.1` 改成 `host.docker.internal`。
- 也可改环境变量 `MEMORY_API_BASE`（若你版本支持），但以 HTTP 节点里最终 URL 为准。

同时在 **LLM** 节点选一个你账号里已配置好的对话模型。

### 4. 验证样例

完整 curl / 对话剧本见 [verification.md](./verification.md)。

最短验收：

1. 调试对话输入：`我把你的药水藏起来了，恶作剧！` → 应走 save，回复承认记下了。
2. 同一会话再问：`今天天气怎么样？` → 应走 recall，提到药水旧账。
3. 看 `backend/data/grudges.json` 是否新增记录。

---

## 可选：接真实 PowerContext

编辑 `backend/.env`（**不要提交真实密钥**）：

```env
POWERCONTEXT_MODE=http
POWERCONTEXT_BASE_URL=http://127.0.0.1:8000
POWERCONTEXT_API_KEY=
POWERCONTEXT_SCOPE=grudge-npc
```

重启 `python main.py`。`/health` 应显示 `"mode": "http"`。

---

## 目录

```
dify/
  README.md
  verification.md
  workflows/grudge-npc.yml
  backend/
    main.py
    smoke_test.py
    memory_store.py
    powercontext_client.py
    requirements.txt
    .env.example
    data/
```
