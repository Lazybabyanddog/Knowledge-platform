<#
.SYNOPSIS
    启动 Knowledge-platform 后端（FastAPI API + Celery worker），当前窗口实时输出 API 日志。

.DESCRIPTION
    1. 校验 ai_platform 环境的 python 是否可用；
    2. 检查端口占用，-Restart 时先结束旧进程；
    3. 未指定 -NoWorker 时，在新窗口拉起 parse 队列的 celery worker；
    4. 前台运行 uvicorn（默认 --reload），日志直接打到本窗口，退出后窗口保持。

.EXAMPLE
    pwsh -File scripts\start-backend.ps1

.EXAMPLE
    pwsh -File scripts\start-backend.ps1 -Restart -Port 8001
#>
[CmdletBinding()]
param(
    [int]$Port = 8000,
    [switch]$Restart,
    [switch]$NoWorker,
    [switch]$NoReload,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'

Set-WindowTitle "Knowledge-platform backend :$Port"
Write-Step "工作目录 $backendDir"

if (-not (Test-Path (Join-Path $backendDir 'app\main.py'))) {
    Write-Warn "未找到 backend\app\main.py，请确认脚本位于项目根目录的 scripts 下"
    Wait-BeforeClose -NoPause:$NoPause
    exit 1
}

$python = Resolve-CondaPython -Root $root
Write-Step "python $python"
& $python --version

# ---- 端口 ----
if ($Restart) {
    Stop-PortOwner -Port $Port
} elseif (Get-PortOwner -Port $Port) {
    Write-Warn "端口 $Port 已被占用，若确认要重启请加 -Restart"
    Wait-BeforeClose -NoPause:$NoPause
    exit 1
}

# ---- Celery worker ----
$workerStarted = $false
if (-not $NoWorker) {
    $workers = Get-CeleryWorkers
    if ($Restart) {
        foreach ($worker in $workers) {
            Write-Warn "结束旧 celery worker $($worker.ProcessId)"
            Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Milliseconds 500
        $workers = @()
    }

    if ($workers) {
        $ids = ($workers | ForEach-Object { $_.ProcessId }) -join ', '
        Write-Step "已存在 celery worker（PID $ids），跳过启动"
    } else {
        $workerScript = Join-Path $PSScriptRoot 'start-worker.ps1'
        $psExe = (Get-Process -Id $PID).Path
        Write-Step "在新窗口启动 celery worker（-Q parse）"
        Start-Process -FilePath $psExe `
            -ArgumentList @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$workerScript`"") `
            -WorkingDirectory $backendDir | Out-Null
        $workerStarted = $true
        Start-Sleep -Seconds 2
    }
}

# ---- API ----
$uvicornArgs = @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', $Port)
if (-not $NoReload) {
    $uvicornArgs += '--reload'
}

Write-Step "启动 API http://127.0.0.1:$Port  （Ctrl+C 停止）"
Write-Host ''

Push-Location $backendDir
try {
    & $python @uvicornArgs
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    if ($workerStarted) {
        Write-Step '停止本次启动的 celery worker'
        foreach ($worker in Get-CeleryWorkers) {
            Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }
    Wait-BeforeClose -NoPause:$NoPause
}

exit $exitCode
