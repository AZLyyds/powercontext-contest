# StepFun · 一键安装依赖并启动 sidecar + Vite
# 用法：在 stepfun 目录执行  .\start.ps1
# 可选参数：-SkipInstall  跳过 npm install

param(
  [switch]$SkipInstall,
  [ValidateSet("memory", "seekdb")]
  [string]$StoreBackend = "memory"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host "==> stepfun root: $Root" -ForegroundColor Cyan

if (-not $SkipInstall) {
  Write-Host "==> npm install (frontend)" -ForegroundColor Cyan
  if (-not (Test-Path ".npmrc")) {
    Set-Content -Path ".npmrc" -Value "legacy-peer-deps=true" -Encoding ascii
  }
  npm install --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) { throw "frontend npm install failed" }

  Write-Host "==> npm install (sidecar)" -ForegroundColor Cyan
  Push-Location "$Root\sidecar"
  npm install --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "sidecar 安装 seekdb 失败时仍可用 STORE_BACKEND=memory 演示 hybridSearch"
  }
  Pop-Location
}

# 确保 Vite 读到 seekdb 代理配置
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "==> 已从 .env.example 生成 .env" -ForegroundColor Yellow
}

$env:STORE_BACKEND = $StoreBackend
$env:PORT = if ($env:PORT) { $env:PORT } else { "8787" }

Write-Host "==> 启动 sidecar (STORE_BACKEND=$StoreBackend, PORT=$($env:PORT))" -ForegroundColor Cyan
$sidecar = Start-Process -FilePath "node" -ArgumentList "server.js" `
  -WorkingDirectory "$Root\sidecar" `
  -PassThru -NoNewWindow

Start-Sleep -Seconds 1
try {
  $health = Invoke-RestMethod -Uri "http://127.0.0.1:$($env:PORT)/health" -TimeoutSec 3
  Write-Host "==> sidecar health: backend=$($health.backend) name=$($health.name)" -ForegroundColor Green
} catch {
  Write-Warning "sidecar /health 暂不可达，请检查 sidecar 窗口输出"
}

Write-Host "==> 启动 Vite (VITE_STORE=seekdb)" -ForegroundColor Cyan
Write-Host "    浏览器打开终端提示的地址；Ctrl+C 结束 Vite 后请手动结束 sidecar (PID $($sidecar.Id))" -ForegroundColor DarkGray

try {
  npm run dev
} finally {
  if ($sidecar -and -not $sidecar.HasExited) {
    Write-Host "==> 停止 sidecar PID $($sidecar.Id)" -ForegroundColor Yellow
    Stop-Process -Id $sidecar.Id -Force -ErrorAction SilentlyContinue
  }
}
