# 只有这些必须你本人动手（其它我已代做）

仓库已更新：`https://github.com/AZLyyds/powercontext-contest`  
分支：`2026-competition-ecosystem`  
最新推送示例：`fb2c3cd0…`（若稍后还有增量以 GitHub 最新 commit 为准）

---

## 1. MiniMax（约 2 分钟）
1. 打开大赛 MiniMax 页，复制 API Key  
2. 在本机执行 `minimax/USER_ONLY.md` 里的命令（`run-sample`，不要 dry-run）  
3. 平台提测：分支 `2026-competition-ecosystem` + 最新 40 位 commit

没有 Key 我无法替你调 MiniMax，计分要求「MiniMax 实际参与」。

## 2. Dify（约 5 分钟）
1. 登录你的 Dify（云或自建）  
2. 导入 `dify/workflows/grudge-npc.yml`  
3. 按 `dify/README.md` 把两个 HTTP 节点指到本机 backend（先 `cd dify/backend && python main.py`）  
4. 选一个已配置的 LLM 模型 → 按 `verification.md` 玩一遍 → 提测

## 3. Qoder（必须你在 Qoder 软件里做，无法代劳）
题面硬性规定「主要开发必须在 Qoder」。  
按 `qoder/README.md`：用 Qoder 开发 → Sites 公开 → 记下 UID → 平台填网站/仓/UID。

## 4. AMD（账号+令牌+算力，约 15 分钟起步）
按 `AMD_USER_SETUP.md`：从 `zihaomu/seekdb` V5 拉出 `amd-cagra-submit`，报名地址必须带只读令牌。  
优化代码可之后再让我改；没有 GPU 实例前没法出正式分。

---

## 已代你做好、一般不用管
| 题 | 状态 |
|----|------|
| 高德 / 魔搭 | 你已提测成功 |
| MiniMax 工程+证据模板 | `minimax/` 已推送；差真实 Key 跑一轮 |
| StepFun | 前端+sidecar 已推送；本机 `stepfun/start.ps1` 可演示 |
| Dify 材料 | DSL+backend+说明已推送；差你导入 |
| Qoder | 清单已写好；开发只能你来 |

StepFun / MiniMax（跑完后）提测同样填：仓 `AZLyyds/powercontext-contest`，分支 `2026-competition-ecosystem`，commit 用 GitHub 该分支最新 SHA。
