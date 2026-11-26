<#
.SYNOPSIS
  _release_state.ps1 的自测：状态往返、损坏容忍、记录追加顺序。

.DESCRIPTION
  不依赖 Pester（宿主可能只有 Pester 3，断言语法与 PS7 不兼容），
  直接用断言函数跑，退出码非零即失败。用法：pwsh -File scripts/tests/test_release_state.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'scripts/_release_state.ps1')

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

$repoRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("kp-release-state-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $repoRoot -Force | Out-Null

try {
    Write-Host "current.json 往返"
    $missing = Read-ReleaseState -RepoRoot $repoRoot
    Assert-True ($null -eq $missing) '文件不存在时返回 null'

    $state = [pscustomobject]@{
        version  = 'v20261125-1'
        previous = 'v20261124-1'
        color    = 'green'
        active   = $true
    }
    Write-ReleaseState -RepoRoot $repoRoot -State $state
    $loaded = Read-ReleaseState -RepoRoot $repoRoot
    Assert-True ($loaded.version -eq 'v20261125-1') '版本号可读回'
    Assert-True ($loaded.color -eq 'green') '颜色可读回'

    Write-Host "损坏文件容忍"
    $statePath = Get-ReleaseStatePath -RepoRoot $repoRoot
    [System.IO.File]::WriteAllText($statePath, '{"version": "broken"', [System.Text.UTF8Encoding]::new($false))
    $broken = Read-ReleaseState -RepoRoot $repoRoot 3>$null
    Assert-True ($null -eq $broken) '半截 JSON 不会让读取方崩溃'

    Write-Host "发布记录追加"
    for ($i = 1; $i -le 3; $i++) {
        Add-ReleaseRecord -RepoRoot $repoRoot -Record (New-ReleaseRecord -Action 'release' -Version "v2026112$i-1" -Color 'blue')
    }
    $history = @(Get-ReleaseHistory -RepoRoot $repoRoot -Limit 10)
    Assert-True ($history.Count -eq 3) '三条记录都被保留'
    Assert-True ($history[-1].version -eq 'v20261123-1') '记录按写入顺序返回'
    Assert-True ($history[-1].action -eq 'release') '记录字段完整'
    Assert-True ($history[-1].mode -eq 'bluegreen') '缺省记录带 bluegreen 模式'
    Assert-True (-not [string]::IsNullOrWhiteSpace($history[-1].recorded_at)) '记录了时间'

    Add-ReleaseRecord -RepoRoot $repoRoot -Record (New-ReleaseRecord -Action 'release' -Version 'v20261124-1' -Color 'blue' -Mode 'inplace')
    $history = @(Get-ReleaseHistory -RepoRoot $repoRoot -Limit 10)
    Assert-True ($history[-1].mode -eq 'inplace') '单色发布记录保留 inplace'

    Write-Host "损坏记录跳过"
    Add-Content -Path (Join-Path $repoRoot 'deploy/state/releases.jsonl') -Value 'not-json' -Encoding utf8
    $history2 = @(Get-ReleaseHistory -RepoRoot $repoRoot -Limit 10 3>$null)
    Assert-True ($history2.Count -eq 4) '坏行被跳过，好记录不受影响'
}
finally {
    Remove-Item $repoRoot -Recurse -Force -ErrorAction SilentlyContinue
}

if ($failures -gt 0) {
    Write-Host "$failures 项断言失败" -ForegroundColor Red
    exit 1
}
Write-Host "全部通过"
exit 0
