<#
.SYNOPSIS
  后台排空旧色 worker：等当前任务结束再退出，超时由看护流程接管。

.DESCRIPTION
  由 release.ps1 以 Start-Process 方式唤起，所以它必须独立可运行，
  且不能把失败抛回给已经被释放的父进程。日志写到 deploy/state/drain.log。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet('blue', 'green')][string]$Color,
    [int]$GraceSeconds = 900,
    # 同色需要排空的 worker 服务名：普通队列与 MinerU 队列各一个进程
    [string[]]$Services = @()
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$logPath = Join-Path $repoRoot 'deploy/state/drain.log'
New-Item -ItemType Directory -Path (Split-Path -Parent $logPath) -Force | Out-Null

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -AsUTC -Format o) [$Color] $Message"
    Add-Content -Path $logPath -Value $line -Encoding utf8
}

if (-not $Services -or $Services.Count -eq 0) {
    $Services = @("worker-$Color", "worker-mineru-$Color")
}

Write-Log "开始排空 $($Services -join ', ')，宽限 ${GraceSeconds}s"

$composeArgs = @(
    'compose',
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $repoRoot 'docker-compose.release.yml'),
    '--profile', $Color,
    'stop', '--timeout', "$GraceSeconds"
) + $Services

& docker @composeArgs *>> $logPath
$code = $LASTEXITCODE
Write-Log "$($Services -join ', ') 排空结束 code=$code"
exit $code
