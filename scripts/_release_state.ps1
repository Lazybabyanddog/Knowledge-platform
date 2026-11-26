<#
.SYNOPSIS
  发布状态的读写：current.json（当前状态）+ releases.jsonl（只追加的历史）。

.DESCRIPTION
  发布工具不能依赖被发布系统自身的可用性——数据库迁移失败时恰恰最需要写记录，
  所以状态放在宿主文件里。写入用临时文件 + rename，中断也不会留下半个 JSON。
  读取容忍文件缺失与损坏：宁可当成"没有上一版本"，也不要让发布脚本起不来。
#>

function Get-ReleaseStatePath {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)
    return (Join-Path $RepoRoot 'deploy/state/current.json')
}

function Read-ReleaseState {
    param([Parameter(Mandatory = $true)][string]$RepoRoot)

    $path = Get-ReleaseStatePath -RepoRoot $RepoRoot
    if (-not (Test-Path $path)) { return $null }

    try {
        return (Get-Content $path -Raw -Encoding utf8 | ConvertFrom-Json)
    }
    catch {
        Write-Warning "current.json 无法解析，按无状态处理: $($_.Exception.Message)"
        return $null
    }
}

function Write-ReleaseState {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$State
    )

    $path = Get-ReleaseStatePath -RepoRoot $RepoRoot
    New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null

    $json = $State | ConvertTo-Json -Depth 8
    $tmp = "$path.tmp"
    [System.IO.File]::WriteAllText($tmp, $json, [System.Text.UTF8Encoding]::new($false))
    # Move(overwrite) 是同卷 rename：读方只会看到旧值或新值，不会是半个文件
    [System.IO.File]::Move($tmp, $path, $true)
}

function Add-ReleaseRecord {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [Parameter(Mandatory = $true)]$Record
    )

    $path = Join-Path $RepoRoot 'deploy/state/releases.jsonl'
    New-Item -ItemType Directory -Path (Split-Path -Parent $path) -Force | Out-Null
    $line = $Record | ConvertTo-Json -Depth 8 -Compress
    Add-Content -Path $path -Value $line -Encoding utf8
}

function Get-ReleaseHistory {
    param(
        [Parameter(Mandatory = $true)][string]$RepoRoot,
        [int]$Limit = 20
    )

    $path = Join-Path $RepoRoot 'deploy/state/releases.jsonl'
    if (-not (Test-Path $path)) { return @() }

    $records = @()
    foreach ($line in Get-Content $path -Encoding utf8) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        try {
            $records += ($line | ConvertFrom-Json)
        }
        catch {
            Write-Warning "跳过损坏的发布记录: $line"
        }
    }
    return @($records | Select-Object -Last $Limit)
}

function New-ReleaseRecord {
    param(
        [string]$Action = 'release',
        [string]$Version = '',
        [string]$Color = '',
        [string]$Result = 'success',
        [string]$Stage = '',
        [string]$Error = '',
        [string]$Operator = '',
        [string]$Previous = '',
        [string[]]$Migrations = @(),
        [bool]$Contract = $false,
        [string]$Reason = '',
        [string]$RollbackOf = '',
        [string]$Mode = 'bluegreen'
    )

    if ([string]::IsNullOrWhiteSpace($Operator)) {
        $Operator = if ($env:USERNAME) { $env:USERNAME } else { 'unknown' }
    }

    return [pscustomobject]@{
        action      = $Action
        version     = $Version
        previous    = $Previous
        color       = $Color
        result      = $Result
        stage       = $Stage
        error       = $Error
        migrations  = @($Migrations)
        # contract 一旦执行，schema 已不可逆，这条记录是回滚门禁的依据
        contract    = $Contract
        # 回滚记录带上"为什么回滚"和"从哪一版回滚"，事后追查不用翻 shell 历史
        reason      = $Reason
        rollback_of = $RollbackOf
        # 发布模式决定回滚方式：蓝绿换色，单色按镜像 tag 原地重建
        mode        = $Mode
        operator    = $Operator
        recorded_at = (Get-Date).ToUniversalTime().ToString('o')
    }
}
