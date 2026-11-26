<#
.SYNOPSIS
  构建前端并把产物落到 deploy/releases/<version>/。

.DESCRIPTION
  版本目录只增不改：同一版本重复构建直接失败，避免覆盖旧页面仍在引用的资源。
  构建后校验 index.html 引用的资源都在同一目录，防止 HTML 与 assets 混版。

.PARAMETER Version
  发布版本号，同时用于目录名。建议 vYYYYMMDD-N。

.PARAMETER Keep
  保留的版本目录数量（含当前），默认 3，够覆盖"切换前打开、切换后才懒加载"的页面。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z._-]+$')]
    [string]$Version,

    [int]$Keep = 3
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $repoRoot 'frontend'
$releases = Join-Path $repoRoot 'deploy/releases'
$target = Join-Path $releases $Version

if (Test-Path $target) {
    throw "版本目录已存在: $target（版本目录只增不改，请换一个版本号）"
}

Write-Host "[build] npm run build ($Version)"
Push-Location $frontend
try {
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "前端构建失败，退出码 $LASTEXITCODE" }
}
finally {
    Pop-Location
}

$dist = Join-Path $frontend 'dist'
$distIndex = Join-Path $dist 'index.html'
if (-not (Test-Path $distIndex)) { throw "构建产物缺少 index.html: $dist" }

New-Item -ItemType Directory -Path $target -Force | Out-Null
Copy-Item -Path (Join-Path $dist '*') -Destination $target -Recurse -Force

# 入口 HTML 与哈希资源必须同目录：这里主动校验，而不是等用户白屏
$indexPath = Join-Path $target 'index.html'
$html = Get-Content $indexPath -Raw -Encoding utf8
$refs = @([regex]::Matches($html, '(?:src|href)="(/?assets/[^"]+)"') | ForEach-Object { $_.Groups[1].Value })

$missing = @()
foreach ($ref in $refs) {
    $relative = $ref.TrimStart('/')
    if (-not (Test-Path (Join-Path $target $relative))) { $missing += $ref }
}
if ($missing.Count -gt 0) {
    throw "index.html 引用了不存在的资源: $($missing -join ', ')"
}

# 保留最近 N 个版本（按目录名倒序），当前版本永远保留
$all = @(Get-ChildItem $releases -Directory | Sort-Object Name -Descending)
$stale = @($all | Where-Object { $_.Name -ne $Version } | Select-Object -Skip ([Math]::Max($Keep - 1, 0)))
foreach ($dir in $stale) {
    Write-Host "[build] 清理旧版本 $($dir.Name)"
    Remove-Item $dir.FullName -Recurse -Force
}

Write-Host "[build] 完成: $target（资源引用 $($refs.Count) 个，清理 $($stale.Count) 个旧版本）"

[pscustomobject]@{
    version = $Version
    path    = $target
    assets  = $refs.Count
    pruned  = @($stale | ForEach-Object { $_.Name })
} | ConvertTo-Json -Depth 4
