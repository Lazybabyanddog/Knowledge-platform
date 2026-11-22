<#
.SYNOPSIS
    启动 Knowledge-platform 前端 Vite 开发服务器，当前窗口实时输出编译与代理日志。

.DESCRIPTION
    1. 检查 node / npm 是否可用；
    2. 首次运行自动 npm install（-SkipInstall 可跳过安装）；
    3. 检查端口占用，-Restart 时先结束旧进程；
    4. 前台运行 npm run dev，退出后窗口保持。

.EXAMPLE
    pwsh -File scripts\start-frontend.ps1

.EXAMPLE
    pwsh -File scripts\start-frontend.ps1 -Restart
#>
[CmdletBinding()]
param(
    [int]$Port = 5173,
    [switch]$Restart,
    [switch]$SkipInstall,
    [switch]$NoPause
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '_common.ps1')

$root = Split-Path -Parent $PSScriptRoot
$frontendDir = Join-Path $root 'frontend'

Set-WindowTitle "Knowledge-platform frontend :$Port"
Write-Step "工作目录 $frontendDir"

if (-not (Test-Path (Join-Path $frontendDir 'package.json'))) {
    Write-Warn '未找到 frontend\package.json，请确认脚本位于项目根目录的 scripts 下'
    Wait-BeforeClose -NoPause:$NoPause
    exit 1
}

$npm = Get-Command npm.cmd -ErrorAction SilentlyContinue
if (-not $npm) {
    $npm = Get-Command npm -ErrorAction SilentlyContinue
}
if (-not $npm) {
    Write-Warn '未找到 npm，请先安装 Node.js'
    Wait-BeforeClose -NoPause:$NoPause
    exit 1
}
Write-Step "npm $($npm.Source)"
& node --version

# ---- 依赖 ----
if (-not (Test-Path (Join-Path $frontendDir 'node_modules'))) {
    if ($SkipInstall) {
        Write-Warn 'node_modules 缺失且指定了 -SkipInstall，vite 启动大概率失败'
    } else {
        Write-Step 'node_modules 缺失，执行 npm install'
        Push-Location $frontendDir
        try {
            & $npm.Source install
            if ($LASTEXITCODE -ne 0) {
                Write-Warn 'npm install 失败'
                Wait-BeforeClose -NoPause:$NoPause
                exit 1
            }
        } finally {
            Pop-Location
        }
    }
}

# ---- 端口 ----
if ($Restart) {
    Stop-PortOwner -Port $Port
} elseif (Get-PortOwner -Port $Port) {
    Write-Warn "端口 $Port 已被占用，若确认要重启请加 -Restart"
    Wait-BeforeClose -NoPause:$NoPause
    exit 1
}

Write-Step "启动 Vite http://localhost:$Port  （Ctrl+C 停止）"
Write-Host ''

Push-Location $frontendDir
try {
    & $npm.Source run dev
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
    Wait-BeforeClose -NoPause:$NoPause
}

exit $exitCode
