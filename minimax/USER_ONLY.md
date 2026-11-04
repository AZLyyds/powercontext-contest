# MiniMax：只有这一步需要你动手

把大赛领到的 MiniMax API Key 设成环境变量后，在本目录运行：

```powershell
$env:MINIMAX_API_KEY="粘贴你的Key"
$env:SEEKDB_MODE="sqlite"          # 若已有 seekdb 改成 mysql 并填 SEEKDB_*
$env:POWERCONTEXT_MODE="file"      # 若已起 PowerContext 改成 http
python -m sdd_context.cli run-sample
```

不要加 `--dry-run-patch`。跑完把 `evidence/minimax_dev_record.json` 里出现真实模型回复即可提测。

Key 从哪来：OceanBase 大赛 MiniMax 赛题页 / MiniMax 控制台。
