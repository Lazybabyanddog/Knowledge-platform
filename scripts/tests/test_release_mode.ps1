<#
.SYNOPSIS
  _release_mode.ps1 的自测：模式优先级、归一化、目标色推导。

.DESCRIPTION
  与 test_release_state.ps1 一样不依赖 Pester，退出码非零即失败。
  用法：pwsh -File scripts/tests/test_release_mode.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'scripts/_release_mode.ps1')

$failures = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        Write-Host "  ok   $Message"
    }
    else {
        Write-Host "  FAIL $Message" -ForegroundColor Red
        $script:failures++
    }
}

Write-Host '模式优先级'
Assert-True ((Resolve-ReleaseMode) -eq 'bluegreen') '命令行与 .env 都为空时默认蓝绿'
Assert-True ((Resolve-ReleaseMode -FromEnv 'inplace') -eq 'inplace') '.env 可以设为单色'
Assert-True ((Resolve-ReleaseMode -Requested 'bluegreen' -FromEnv 'inplace') -eq 'bluegreen') '命令行覆盖 .env'
Assert-True ((Resolve-ReleaseMode -FromEnv 'bluegreen') -eq 'bluegreen') '空命令行不掩盖 .env 值'

Write-Host '归一化'
Assert-True ((Resolve-ReleaseMode -Requested ' InPlace ') -eq 'inplace') '大小写与空白被归一化'
Assert-True ((Resolve-ReleaseMode -Requested 'blue-green') -eq 'bluegreen') 'blue-green 视作 bluegreen'

Write-Host '非法模式'
$threw = $false
try {
    Resolve-ReleaseMode -Requested 'canary' | Out-Null
}
catch {
    $threw = $true
}
Assert-True $threw '未知模式直接报错，不会静默降级'

Write-Host '目标色'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'bluegreen' -ActiveColor 'blue') -eq 'green') '蓝绿：blue -> green'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'bluegreen' -ActiveColor 'green') -eq 'blue') '蓝绿：green -> blue'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'bluegreen' -ActiveColor 'none') -eq 'blue') '蓝绿：首次发布落 blue'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'inplace' -ActiveColor 'green') -eq 'green') '单色：沿用当前色 green'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'inplace' -ActiveColor 'blue') -eq 'blue') '单色：沿用当前色 blue'
Assert-True ((Resolve-ReleaseTargetColor -Mode 'inplace' -ActiveColor 'none') -eq 'blue') '单色：首次发布落 blue'

if ($failures -gt 0) {
    Write-Host "$failures 项断言失败" -ForegroundColor Red
    exit 1
}
Write-Host '全部通过'
exit 0
