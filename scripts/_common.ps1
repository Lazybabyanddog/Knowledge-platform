# 启动脚本公共函数：被 start-backend.ps1 / start-worker.ps1 / start-frontend.ps1 点引用。
# 只放与业务无关的工具函数，避免三个脚本各写一份。

Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Warn {
    param([string]$Message)
    Write-Host "!!! $Message" -ForegroundColor Yellow
}

function Set-WindowTitle {
    param([string]$Title)
    try {
        $Host.UI.RawUI.WindowTitle = $Title
    } catch {
        # 非交互宿主（管道 / CI）没有窗口标题，忽略
    }
}

function Get-PortOwner {
    param([int]$Port)

    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
}

function Stop-PortOwner {
    <#
    .SYNOPSIS
        结束占用指定端口的进程，用于 -Restart 场景。
    #>
    param([int]$Port)

    $owners = Get-PortOwner -Port $Port
    if (-not $owners) {
        return
    }

    foreach ($processId in $owners) {
        $proc = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -eq $proc) { continue }
        Write-Warn "结束占用端口 $Port 的进程 $($proc.Id) $($proc.ProcessName)"
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }

    for ($i = 0; $i -lt 20; $i++) {
        if (-not (Get-PortOwner -Port $Port)) { return }
        Start-Sleep -Milliseconds 200
    }
    Write-Warn "端口 $Port 仍未释放，继续启动可能失败"
}

function Resolve-CondaPython {
    <#
    .SYNOPSIS
        定位 ai_platform 环境的 python，优先级：AIP_PYTHON > 当前 conda 环境 > 已知安装路径 > .venv > PATH。
    #>
    param([Parameter(Mandatory = $true)][string]$Root)

    if ($env:AIP_PYTHON -and (Test-Path $env:AIP_PYTHON)) {
        return $env:AIP_PYTHON
    }

    $candidates = @()
    if ($env:CONDA_PREFIX) {
        $candidates += (Join-Path $env:CONDA_PREFIX 'python.exe')
    }
    $candidates += @(
        'D:\developTool\Minianconda\envs\ai_platform\python.exe',
        (Join-Path $Root '.venv\Scripts\python.exe')
    )

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) {
        return $onPath.Source
    }

    throw '找不到 python，请设置环境变量 AIP_PYTHON 指向 ai_platform 环境的 python.exe'
}

function Get-CeleryWorkers {
    <#
    .SYNOPSIS
        找出本项目的 celery worker 进程（按命令行匹配 celery_app 与 worker 子命令）。
    #>
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -like '*app.workers.celery_app*' -and
            $_.CommandLine -like '*worker*'
        }
}

function Wait-BeforeClose {
    <#
    .SYNOPSIS
        服务退出后停住窗口，方便回看输出；-NoPause 时跳过（便于脚本化调试）。
    #>
    param([switch]$NoPause)

    if ($NoPause) { return }
    Write-Host ''
    Write-Host '服务已退出，按回车关闭窗口……' -ForegroundColor DarkGray
    [void](Read-Host)
}
