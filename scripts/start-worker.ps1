<#
.SYNOPSIS
    单独启动 Knowledge-platform 的 Celery worker（parse 队列：解析 + 抽取 + 索引）。

.DESCRIPTION
    一般由 start-backend.ps1 在新窗口自动调用；也可以单独运行（例如批量重建索引时另起一个 worker）。
    前台运行，日志直接输出到本窗口，退出后窗口保持。

.EXAMPLE
    pwsh -File scripts\start-worker.ps1

.EXAMPLE
    # MinerU 解析独占一个队列，需要另开窗口单独启动（配合 MINERU_BASE_URL 使用）
    pwsh -File scripts\start-worker.ps1 -Queue parse.mineru
#>
[CmdletBinding()]
param(
    [string]$Queue = 'parse',
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $root 'backend'

Set-WindowTitle "Knowledge-platform celery worker ($Queue)"
Write-Step "工作目录 $backendDir"

$python = Resolve-CondaPython -Root $root
Write-Step "python $python"
& $python --version

# Windows 上默认 prefork 池依赖 billiard 的 fork 语义，这里用 solo 保证任务串行可观测
$celeryArgs = @(
    '-m', 'celery',
    '-A', 'app.workers.celery_app:celery_app',
    'worker',
    '-Q', $Queue,
    '-l', 'info',
    '--pool=solo'
)

Write-Step "启动 worker 队列 $Queue （Ctrl+C 停止）"
Write-Host ''

Push-Location $backendDir
try {
    & $python @celeryArgs
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    Wait-BeforeClose -NoPause:$NoPause
}

exit $exitCode
