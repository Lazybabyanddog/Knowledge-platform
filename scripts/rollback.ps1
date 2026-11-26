<#
.SYNOPSIS
  回退到上一个成功版本：蓝绿换色，单色按镜像 tag 原地重建；不改 schema。

.DESCRIPTION
  回滚边界：只要本次发布执行过 contract 迁移（删除/收紧），就不能回滚——
  数据已经按新结构写过，回退代码只会让旧版读到不认识的结构。这种情况只能前向修复。

  回滚方式由 current.json 的 mode 决定（旧状态没有该字段时按 bluegreen 处理）：
  - bluegreen：起回上一色、确认就绪、重写 active.conf、reload，再停当前色；
  - inplace：颜色不变，按上一版本的镜像 tag 原地重建容器，再重写 active.conf。
  前端版本目录还在（保留 3 个），所以静态资源同步回退。

.PARAMETER To
  指定回滚到的版本号；缺省用 current.json 里的 previous。

.PARAMETER Reason
  回滚原因，会写进发布记录。
#>
[CmdletBinding()]
param(
    [string]$To = '',
    [string]$Reason = '未填写原因'
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot '_release_state.ps1')
. (Join-Path $PSScriptRoot '_release_mode.ps1')

$composeArgs = @(
    '-f', (Join-Path $repoRoot 'docker-compose.yml'),
    '-f', (Join-Path $repoRoot 'docker-compose.release.yml')
)

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeCommand)

    & docker compose @composeArgs @ComposeCommand
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($ComposeCommand -join ' ') 失败（退出码 $LASTEXITCODE）"
    }
}

function Get-EnvValue {
    param([string]$Name, [string]$Default = '')

    $path = Join-Path $repoRoot '.env'
    if (-not (Test-Path $path)) { return $Default }

    foreach ($line in Get-Content $path -Encoding utf8) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)$") {
            $value = $matches[1].Trim()
            if ($value.Length -ge 2 -and $value.StartsWith('"') -and $value.EndsWith('"')) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return $Default
}

function Get-ApiPort {
    param([string]$Color)

    # 宿主映射端口可改（默认 8000/8001），探测必须跟着 .env 走，否则端口一改回滚就探错地方
    if ($Color -eq 'blue') { return [int](Get-EnvValue -Name 'BLUE_API_PORT' -Default '8000') }
    return [int](Get-EnvValue -Name 'GREEN_API_PORT' -Default '8001')
}

function Set-ActiveConf {
    param([string]$Color, [string]$ReleaseVersion)

    $content = @"
# 由 scripts/release.ps1 / rollback.ps1 生成，请勿手工编辑。
map `$server_name `$api_upstream {
    default api-${Color}:8000;
}

map `$server_name `$frontend_root {
    default /srv/www/$ReleaseVersion;
}
"@

    $path = Join-Path $repoRoot 'deploy/gateway/conf.d/active.conf'
    $tmp = "$path.tmp"
    [System.IO.File]::WriteAllText($tmp, $content, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::Move($tmp, $path, $true)
}

function Wait-ApiReady {
    param([int]$Port, [int]$TimeoutSeconds = 60)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $last = '尚未收到响应'
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/readyz" -TimeoutSec 5 -SkipHttpErrorCheck
            $last = "$($resp.StatusCode) $($resp.Content)"
            if ($resp.StatusCode -eq 200) {
                $data = ($resp.Content | ConvertFrom-Json).data
                if ($data.status -ne 'unavailable') { return $data }
            }
        }
        catch {
            $last = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "回滚目标 readyz 未通过（最后一次: $last）"
}

$state = Read-ReleaseState -RepoRoot $repoRoot
if (-not $state) { throw '没有发布状态（deploy/state/current.json），无法回滚' }

$currentVersion = [string]$state.version
$currentColor = [string]$state.color
$mode = Resolve-ReleaseMode -Requested ([string]$state.mode) -FromEnv ''
$inplace = $mode -eq 'inplace'
$targetVersion = if ($To) { $To } else { [string]$state.previous }
$targetColor = if ($inplace) { $currentColor } else { [string]$state.previous_color }

if ($inplace -and [string]::IsNullOrWhiteSpace($currentColor)) {
    # 单色模式没有"另一色"可推，颜色只能来自 current.json，缺了就无从重建
    throw 'current.json 缺少 color，无法在单色模式下原地回滚'
}

if ([string]::IsNullOrWhiteSpace($targetVersion)) {
    throw "没有可回滚的上一版本（当前 $currentVersion 是首次发布）"
}
if ($targetVersion -eq $currentVersion -and $targetColor -eq $currentColor) {
    throw "回滚目标与当前版本、颜色都相同: $currentVersion/$currentColor"
}
if ($targetVersion -eq $currentVersion -and -not $inplace) {
    # 同版本重发过（上一次也是这一版），回滚等价于换回上一颜色，代码不变
    Write-Host "[rollback] 目标与当前都是 $currentVersion，按换色回滚到 $targetColor"
}

# contract 检查放在最前面：schema 已不可逆时，连切流都不该尝试
$contractEvidence = $null
if ($state.allow_contract) {
    $contractEvidence = "current.json 标记本次发布 allow_contract=true"
}
else {
    $history = @(Get-ReleaseHistory -RepoRoot $repoRoot -Limit 20)
    $latestSuccess = $history | Where-Object { $_.action -eq 'release' -and $_.result -eq 'success' -and $_.version -eq $currentVersion } | Select-Object -Last 1
    if ($latestSuccess -and $latestSuccess.contract -eq $true) {
        $contractEvidence = "发布记录显示 $currentVersion 执行过 contract 迁移"
    }
}

if ($contractEvidence) {
    Write-Host "[rollback] 拒绝回滚：$contractEvidence" -ForegroundColor Red
    Write-Host '[rollback] 删除/收紧过的结构无法用旧代码读取，只能前向修复并重新发布。' -ForegroundColor Yellow
    Add-ReleaseRecord -RepoRoot $repoRoot -Record (New-ReleaseRecord -Action 'rollback' -Version $targetVersion `
            -Color $currentColor -Result 'rejected' -Stage 'contract-check' `
            -Error $contractEvidence -Previous $currentVersion -Reason $Reason `
            -RollbackOf $currentVersion -Mode $mode)
    exit 1
}

if (-not $To -and [string]::IsNullOrWhiteSpace($targetColor)) {
    throw "状态里没有上一版本的颜色，请用 -To <version> 明确指定"
}

$targetDir = Join-Path $repoRoot "deploy/releases/$targetVersion"
if (-not (Test-Path $targetDir)) {
    throw "回滚目标的前端目录不存在: $targetDir（版本目录已被清理，需要重新构建该版本）"
}

if ($inplace) {
    # 单色模式只能按镜像 tag 重建，先确认 tag 还在，避免重建到一半才发现没有可用的旧镜像
    & docker image inspect "kp-backend:$targetVersion" *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "回滚目标镜像不存在: kp-backend:$targetVersion（单色模式按镜像 tag 原地回退）"
    }
}

$stage = 'start-target'
try {
    Write-Host "[rollback] $currentVersion/$currentColor -> $targetVersion/$targetColor（$mode）"

    # 旧色可能已被停掉，先按原镜像 tag 起回来（tag 还在，不需要重建）
    # worker 一并起回：只起 API 会让解析/索引/抽取任务在回滚后没人消费
    $env:RELEASE_VERSION = $targetVersion
    $stage = 'start-target'
    if ($inplace) {
        Write-Host "[rollback] 原地重建 $targetColor 到 $targetVersion（重建期间不可用）"
        Invoke-Compose --profile $targetColor up --detach --no-deps --force-recreate `
            "api-$targetColor" "worker-$targetColor" "worker-mineru-$targetColor" "beat-$targetColor"
    }
    else {
        Invoke-Compose --profile $targetColor up --detach --no-deps "api-$targetColor" "worker-$targetColor" "worker-mineru-$targetColor"
    }

    $stage = 'readyz'
    $port = Get-ApiPort -Color $targetColor
    $ready = Wait-ApiReady -Port $port -TimeoutSeconds 60
    Write-Host "[rollback] 目标 readyz 通过 status=$($ready.status)"

    $stage = 'switch'
    Set-ActiveConf -Color $targetColor -ReleaseVersion $targetVersion
    Invoke-Compose exec -T gateway nginx -t
    Invoke-Compose exec -T gateway nginx -s reload

    $health = $null
    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 1
        try {
            $health = (Invoke-RestMethod -Uri 'http://127.0.0.1:8080/healthz' -TimeoutSec 5).data
            if ($health.color -eq $targetColor) { break }
        }
        catch {
            Write-Verbose "gateway 检查失败: $($_.Exception.Message)"
        }
    }
    if ($null -eq $health -or $health.color -ne $targetColor) {
        throw "回滚后 gateway 未指向 $targetColor（当前: $($health.color)）"
    }

    $stage = 'drain'
    if ($inplace) {
        # 单色模式没有第二个颜色：容器已随重建整体替换，不存在需要排空的旧实例
        Write-Host '[rollback] 单色模式：容器已原地替换，无需排空'
    }
    else {
        # beat 同一时刻只能有一个：先停当前色，再起目标色
        Invoke-Compose --profile $currentColor stop "beat-$currentColor"
        Invoke-Compose --profile $targetColor up --detach --no-deps "beat-$targetColor"
        Invoke-Compose --profile $currentColor stop "api-$currentColor"

        # 当前色 worker 后台排空：回滚不该把正在跑的长任务硬切掉
        $grace = [int](Get-EnvValue -Name 'WORKER_DRAIN_GRACE' -Default '900')
        Write-Host "[rollback] $currentColor 色 worker 后台排空（宽限 ${grace}s）"
        Start-Process -FilePath 'pwsh' -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-File', (Join-Path $PSScriptRoot '_drain-worker.ps1'),
            '-Color', $currentColor, '-GraceSeconds', $grace
        ) | Out-Null
    }

    $record = New-ReleaseRecord -Action 'rollback' -Version $targetVersion -Color $targetColor `
        -Result 'success' -Previous $currentVersion -Reason $Reason -RollbackOf $currentVersion -Mode $mode
    Add-ReleaseRecord -RepoRoot $repoRoot -Record $record

    Write-ReleaseState -RepoRoot $repoRoot -State ([pscustomobject]@{
            version        = $targetVersion
            color          = $targetColor
            mode           = $mode
            previous       = $currentVersion
            previous_color = $currentColor
            switched_at    = (Get-Date).ToUniversalTime().ToString('o')
            operator       = $record.operator
            migrations     = @()
            # 回滚不改变 schema，contract 标记沿用当前版本的事实
            allow_contract = [bool]$state.allow_contract
        })

    Write-Host "[rollback] 完成: $targetVersion 已在 $targetColor 上服务（$mode）"
    [pscustomobject]@{
        version  = $targetVersion
        color    = $targetColor
        mode     = $mode
        previous = $currentVersion
        reason   = $Reason
        result   = 'success'
    } | ConvertTo-Json -Depth 4
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Host "[rollback] 失败于 $stage : $message" -ForegroundColor Red
    Add-ReleaseRecord -RepoRoot $repoRoot -Record (New-ReleaseRecord -Action 'rollback' -Version $targetVersion `
            -Color $targetColor -Result 'failed' -Stage $stage -Error $message -Previous $currentVersion `
            -Reason $Reason -RollbackOf $currentVersion -Mode $mode)
    exit 1
}
