# AMD 赛题：我已尽量准备，剩下必须你做的

## 背景
正式基线在：`zihaomu/seekdb` 分支 `amd-oceanbase-official-contest-base-v5`  
冻结 commit：`634888d9b037153fa143d2c913ba4b25503e1d62`

你账号下已有 `AZLyyds/seekdb` / `seekdb-amd-contest`，但父仓是 `oceanbase/seekdb`，**不一定**带齐 AMD V5 对象。创建 `amd-cagra-submit` 分支若失败，请按下面做一次（只需一次）。

## 你需要做的（约 10 分钟，在能访问 GitHub 的机器上）

```powershell
# 1) 单独 clone 比赛基线（不要用旧 oceanbase/seekdb fork 硬改）
git clone https://github.com/zihaomu/seekdb.git seekdb-amd-v5
cd seekdb-amd-v5
git fetch origin amd-oceanbase-official-contest-base-v5 --tags
git switch -c amd-cagra-submit origin/amd-oceanbase-official-contest-base-v5

# 2) 推到你自己的私有仓（可新建 AZLyyds/seekdb-amd-cagra）
# 在 GitHub 网页新建空私有仓库后：
git remote rename origin upstream-contest
git remote add origin https://github.com/AZLyyds/<你的私有仓名>.git
git push -u origin amd-cagra-submit
```

## 报名仓库地址格式（错了直接 failMsg=4）

```text
https://<GitHub用户名>:<只读Fine-grained令牌>@github.com/<所有者>/<仓库名>
```

令牌权限：仅该仓 **Contents: Read-only**。

## 算力
在大赛 AMD 云端领 Credits / 起实例；本机没有 W7900D 也能开发，但正式分只看云端评测。

## 优化方向（有实例后再做）
改答题仓里 CAGRA 构建路径（可动 seekdb + `tools/hipvs_bridge/`），不动 hipVS 库本身。先提交未改基线只能拿正确性 4 分。
