<#
.SYNOPSIS
  执行一次发布：构建 → 迁移 → 起新色/原地重建 → 验证 → 切流 → 排空。

.DESCRIPTION
  两种发布模式（-Mode，缺省取 .env 的 RELEASE_MODE，都未配置即 bluegreen）：
  - bluegreen：两色并存，新色通过验证才切流，失败清理新色、线上不受影响（默认）；
  - inplace：只维护当前色，停旧起新，有停机窗口；失败自动换回上一版本镜像。

  共同的设计要点：
  - 迁移只跑 expand（check_migrations.py 门禁），失败则不动线上；
  - 通过 /readyz 与只读冒烟才算成功；
  - 切流只重写 active.conf 并 reload，不重启容器；
  - 蓝绿模式下旧 API 随 stop_grace_period 排空存量 SSE，旧 worker 后台延迟下线，避免长任务被打断。

.PARAMETER Version
  发布版本号，同时作为镜像 tag 与前端版本目录名，建议 vYYYYMMDD-N。

.PARAMETER SkipBuild
  跳过镜像与前端构建（复用已存在的产物），用于演练与重试。

.PARAMETER AllowContract
  明确本次包含 contract 迁移（删除/收紧类）。执行后该版本不可回滚。

.PARAMETER Mode
  bluegreen（蓝绿，默认）或 inplace（单色原地重建）。命令行优先于 .env 的 RELEASE_MODE。

.PARAMETER ReadyTimeoutSeconds
  等待 /readyz 通过的上限，默认 90 秒。
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Za-z._-]+$')]
    [string]$Version,

    [switch]$SkipBuild,
    [switch]$AllowContract,

    [ValidateSet('bluegreen', 'inplace')]
    [string]$Mode = '',

    [int]$ReadyTimeoutSeconds = 90
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

function Set-ActiveConf {
    param([string]$Color, [string]$ReleaseVersion)

    # 两个切换点写在同一份文件里：一次 rename 生效，不存在"API 换了前端没换"的中间态
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
    Write-Host "[release] active.conf -> api-$Color / $ReleaseVersion"
}

function Invoke-ComposeReload {
    # 先校验语法：配置写错时留在旧配置上继续服务，而不是把网关切坏
    Invoke-Compose exec -T gateway nginx -t
    Invoke-Compose exec -T gateway nginx -s reload
}

function Wait-ApiReady {
    param([int]$Port, [int]$TimeoutSeconds = 90)

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
    throw "readyz 在 ${TimeoutSeconds}s 内未通过（最后一次: $last）"
}

function Invoke-Smoke {
    param([string]$BaseUrl, [string]$Username, [string]$Password)

    $loginBody = @{ username = $Username; password = $Password } | ConvertTo-Json
    $login = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/auth/login" `
        -ContentType 'application/json' -Body $loginBody -TimeoutSec 20
    $token = $login.data.access_token
    if ([string]::IsNullOrWhiteSpace($token)) { throw '冒烟失败：登录响应缺少 access_token' }

    $headers = @{ Authorization = "Bearer $token" }
    $me = Invoke-RestMethod -Method Get -Uri "$BaseUrl/api/v1/auth/me" -Headers $headers -TimeoutSec 20
    if (-not $me.data.id) { throw '冒烟失败：/auth/me 未返回用户' }

    $searchBody = @{ query = 'release smoke'; top_k = 3 } | ConvertTo-Json
    $search = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/retrieval/search" `
        -Headers $headers -ContentType 'application/json' -Body $searchBody -TimeoutSec 60
    if ($null -eq $search.data) { throw '冒烟失败：检索响应缺少 data' }

    return [pscustomobject]@{ user = $me.data.username; hits = $search.data.total }
}

function Get-ApiPort {
    param([string]$Color)
    if ($Color -eq 'blue') { return [int](Get-EnvValue -Name 'BLUE_API_PORT' -Default '8000') }
    return [int](Get-EnvValue -Name 'GREEN_API_PORT' -Default '8001')
}

function Restore-PreviousInPlace {
    <#
    .SYNOPSIS
        单色模式下失败时，用上一版本镜像把同色容器重建回来。

    .DESCRIPTION
    没有备用色，无法像蓝绿那样"保留线上、清理新色"：新容器已经把旧容器替换掉了，
    唯一的退路是按上一个镜像 tag 原地重建。失败也只做记录与告警，不再重试，
    避免在已经异常的环境里反复重建。
    #>
    param(
        [Parameter(Mandatory = $true)][string]$Color,
        [string]$PreviousVersion = '',
        [int]$TimeoutSeconds = 90
    )

    if ([string]::IsNullOrWhiteSpace($PreviousVersion)) {
        Write-Host '[release] 首次原地发布失败，没有上一版本镜像可回退，请人工处理' -ForegroundColor Red
        return
    }

    Write-Host "[release] 单色模式没有备用色，自动回退到 $PreviousVersion" -ForegroundColor Yellow
    try {
        $env:RELEASE_VERSION = $PreviousVersion
        Invoke-Compose --profile $Color up --detach --no-deps --force-recreate `
            "api-$Color" "worker-$Color" "worker-mineru-$Color" "beat-$Color"
        $restored = Wait-ApiReady -Port (Get-ApiPort -Color $Color) -TimeoutSeconds $TimeoutSeconds

        # 静态资源也要退回去：镜像换了旧版、前端却留在新版目录，页面会调用不存在的接口
        if (Test-Path (Join-Path $repoRoot "deploy/releases/$PreviousVersion")) {
            Set-ActiveConf -Color $Color -ReleaseVersion $PreviousVersion
            Invoke-ComposeReload
        }
        else {
            Write-Host "[release] 上一版本前端目录已清理，静态资源仍指向 $Version" -ForegroundColor Yellow
        }

        Write-Host "[release] 已回退到 $PreviousVersion status=$($restored.status)，current.json 未改动"
    }
    catch {
        Write-Host "[release] 自动回退失败: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "[release] current.json 仍记录 $PreviousVersion，线上实际状态需人工确认" -ForegroundColor Red
    }
}

# ---------------------------------------------------------------- 预检

$stage = 'preflight'
$migrationFiles = @()
$switched = $false
$mode = 'bluegreen'
$targetColor = 'blue'
$previousState = $null
$previousVersion = ''
$activeColor = 'none'

try {
    Write-Host "[release] 版本 $Version"

    foreach ($required in @('.env', 'docker-compose.yml', 'docker-compose.release.yml')) {
        if (-not (Test-Path (Join-Path $repoRoot $required))) { throw "缺少必需文件: $required" }
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '未找到 docker 命令' }

    $previousState = Read-ReleaseState -RepoRoot $repoRoot
    if ($previousState) {
        $previousVersion = [string]$previousState.version
        $activeColor = [string]$previousState.color
    }

    # 模式解析放在读状态之后：inplace 的目标色取决于当前在服务的颜色
    $mode = Resolve-ReleaseMode -Requested $Mode -FromEnv (Get-EnvValue -Name 'RELEASE_MODE' -Default '')
    $targetColor = Resolve-ReleaseTargetColor -Mode $mode -ActiveColor $activeColor
    Write-Host "[release] 模式 $mode，当前 $(if ($activeColor -eq 'none') { '无（首次发布）' } else { "$activeColor/$previousVersion" }) -> 目标 $targetColor/$Version"
    if ($mode -eq 'inplace') {
        Write-Host '[release] 单色模式：重建期间服务不可用，失败会用上一版本镜像原地回退' -ForegroundColor Yellow
    }

    $env:RELEASE_VERSION = $Version

    $running = @(Invoke-Compose ps --services --filter status=running)
    foreach ($name in @('postgres', 'redis', 'silo', 'neo4j')) {
        if ($running -notcontains $name) { throw "中间件 $name 未运行，先执行 docker compose up --detach" }
    }
    if ($running -notcontains 'gateway') { throw 'gateway 未运行，先执行 docker compose --profile blue up --detach gateway' }

    $targetDir = Join-Path $repoRoot "deploy/releases/$Version"
    if ($SkipBuild -and -not (Test-Path $targetDir)) {
        throw "跳过构建但前端目录不存在: $targetDir"
    }

    # 迁移清单只用于记录，读不到就留空，不阻塞发布
    try {
        $migrationFiles = @(
            & git status --porcelain -- backend/alembic/versions |
                Where-Object { $_.Trim() } |
                ForEach-Object { $_.Substring(3).Trim() }
        )
    }
    catch {
        Write-Warning "读取迁移清单失败（不影响发布）: $($_.Exception.Message)"
    }

    # ------------------------------------------------------------ 构建

    if (-not $SkipBuild) {
        $stage = 'build'
        Write-Host '[release] 构建后端镜像'
        # 慢网/内网可用环境变量注入镜像源，Dockerfile 里有对应 ARG
        $buildArgs = @('build', '-t', "kp-backend:$Version")
        if ($env:PIP_INDEX_URL) { $buildArgs += @('--build-arg', "PIP_INDEX_URL=$env:PIP_INDEX_URL") }
        if ($env:PIP_EXTRA_INDEX_URL) { $buildArgs += @('--build-arg', "PIP_EXTRA_INDEX_URL=$env:PIP_EXTRA_INDEX_URL") }
        $buildArgs += (Join-Path $repoRoot 'backend')
        & docker @buildArgs
        if ($LASTEXITCODE -ne 0) { throw "后端镜像构建失败（退出码 $LASTEXITCODE）" }

        Write-Host '[release] 构建前端并落到版本目录'
        & (Join-Path $PSScriptRoot 'build-frontend.ps1') -Version $Version -Keep 3
        if ($LASTEXITCODE -ne 0) { throw "前端构建失败（退出码 $LASTEXITCODE）" }
    }

    # ------------------------------------------------------------ 迁移

    $stage = 'migration-gate'
    $checker = Join-Path $repoRoot 'scripts/check_migrations.py'
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $pythonExe) { throw '未找到 python，无法执行迁移门禁' }

    $gateArgs = @($checker)
    if ($AllowContract) { $gateArgs += '--allow-contract' }
    & $pythonExe @gateArgs
    if ($LASTEXITCODE -ne 0) { throw "迁移门禁未通过（退出码 $LASTEXITCODE）：本次发布只允许 expand 变更" }

    $stage = 'migrate'
    Write-Host '[release] 执行 expand 迁移'
    Invoke-Compose --profile tools run --rm --no-deps migrate

    # ------------------------------------------------------------ 启动新色

    $stage = 'start-target'
    if ($mode -eq 'inplace') {
        # 单色：直接替换现有容器，compose 按服务逐个"停旧再起新"，中间存在停机窗口。
        # beat 一并重建，避免旧镜像的 beat 继续投放周期任务。
        Write-Host "[release] 原地重建 $targetColor 色实例（单色模式）"
        Invoke-Compose --profile $targetColor up --detach --no-deps --force-recreate `
            "api-$targetColor" "worker-$targetColor" "worker-mineru-$targetColor" "beat-$targetColor"
    }
    else {
        Write-Host "[release] 启动 $targetColor 色实例"
        # worker-mineru 与 worker 同色同生：MinerU 队列也要有新色的消费者，否则切流后新任务无人接
        Invoke-Compose --profile $targetColor up --detach --no-deps "api-$targetColor" "worker-$targetColor" "worker-mineru-$targetColor"
    }

    # ------------------------------------------------------------ 验证

    $stage = 'readyz'
    $port = Get-ApiPort -Color $targetColor
    $ready = Wait-ApiReady -Port $port -TimeoutSeconds $ReadyTimeoutSeconds
    Write-Host "[release] readyz 通过 status=$($ready.status)"

    $stage = 'smoke'
    $smokeUser = Get-EnvValue -Name 'SEED_ADMIN_USERNAME' -Default 'admin'
    $smokePassword = Get-EnvValue -Name 'SEED_ADMIN_PASSWORD' -Default ''
    if ([string]::IsNullOrWhiteSpace($smokePassword)) { throw '缺少 SEED_ADMIN_PASSWORD，无法执行只读冒烟' }
    $smoke = Invoke-Smoke -BaseUrl "http://127.0.0.1:$port" -Username $smokeUser -Password $smokePassword
    Write-Host "[release] 冒烟通过 user=$($smoke.user) hits=$($smoke.hits)"

    # ------------------------------------------------------------ 切流

    $stage = 'switch'
    # 单色模式下目标色就是当前色，这里不改流量，只保证 active.conf 与前端目录和版本一致
    Set-ActiveConf -Color $targetColor -ReleaseVersion $Version
    Invoke-ComposeReload
    $switched = $true

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
        throw "切流后 gateway 未指向 $targetColor（当前: $($health.color)）"
    }
    Write-Host "[release] 切流完成，网关现在指向 $targetColor/$Version"

    # ------------------------------------------------------------ 排空

    $stage = 'drain'
    if ($mode -eq 'inplace') {
        Write-Host '[release] 单色模式：容器已随重建替换，无需排空旧色'
    }
    elseif ($activeColor -ne 'none') {
        # beat 必须先停旧再起新，锁只是兜底，顺序才是主要保证
        Write-Host "[release] 切换 beat：停 $activeColor -> 起 $targetColor"
        Invoke-Compose --profile $activeColor stop "beat-$activeColor"
        Invoke-Compose --profile $targetColor up --detach --no-deps "beat-$targetColor"

        Write-Host "[release] 排空旧 API（等存量请求与 SSE 结束）"
        Invoke-Compose --profile $activeColor stop "api-$activeColor"

        # worker 不进接流路径：让它在后台按宽限排空，避免几十分钟的长任务被硬切
        $grace = [int](Get-EnvValue -Name 'WORKER_DRAIN_GRACE' -Default '900')
        $drainScript = Join-Path $PSScriptRoot '_drain-worker.ps1'
        Write-Host "[release] 旧 worker 后台排空（宽限 ${grace}s）"
        Start-Process -FilePath 'pwsh' -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-File', $drainScript,
            '-Color', $activeColor,
            '-GraceSeconds', $grace
        ) | Out-Null
    }

    # ------------------------------------------------------------ 记录

    $record = New-ReleaseRecord -Action 'release' -Version $Version -Color $targetColor `
        -Result 'success' -Previous $previousVersion -Migrations $migrationFiles `
        -Contract:$AllowContract -Mode $mode
    Add-ReleaseRecord -RepoRoot $repoRoot -Record $record

    Write-ReleaseState -RepoRoot $repoRoot -State ([pscustomobject]@{
            version        = $Version
            color          = $targetColor
            mode           = $mode
            previous       = $previousVersion
            previous_color = $activeColor
            switched_at    = (Get-Date).ToUniversalTime().ToString('o')
            operator       = $record.operator
            migrations     = @($migrationFiles)
            allow_contract = [bool]$AllowContract
        })

    Write-Host "[release] 完成: $Version 已在 $targetColor 上服务（$mode）"
    [pscustomobject]@{
        version  = $Version
        color    = $targetColor
        mode     = $mode
        previous = $previousVersion
        ready    = $ready.status
        smoke    = $smoke.user
        result   = 'success'
    } | ConvertTo-Json -Depth 4
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Host "[release] 失败于 $stage : $message" -ForegroundColor Red

    # 只有"新色已经起来"之后的失败才需要清理。preflight/build/迁移阶段线上根本没动，
    # 此时 targetColor 可能正好是当前服务色，盲目清理会把正在服务的线上停掉
    $touchedTarget = $stage -in @('start-target', 'readyz', 'smoke', 'switch')

    if ($mode -eq 'inplace' -and $touchedTarget) {
        # 单色模式没有"保留线上"的余地：容器已经被替换，只能按旧 tag 重建回去
        Restore-PreviousInPlace -Color $targetColor -PreviousVersion $previousVersion `
            -TimeoutSeconds $ReadyTimeoutSeconds
    }
    elseif ($switched) {
        # 已经切了流量：不能停新色，只能等人工回滚
        Write-Host "[release] 流量已切换到 $targetColor，请执行 scripts/rollback.ps1 回退" -ForegroundColor Yellow
    }
    elseif ($touchedTarget -and $targetColor -ne $activeColor) {
        Write-Host "[release] 线上未受影响，清理未接流量的 $targetColor 实例" -ForegroundColor Yellow
        try {
            Invoke-Compose --profile $targetColor stop "api-$targetColor" "worker-$targetColor" "worker-mineru-$targetColor" "beat-$targetColor"
        }
        catch {
            Write-Warning "清理 $targetColor 失败: $($_.Exception.Message)"
        }
    }
    else {
        Write-Host "[release] 线上未受影响（失败于 $stage，未停任何容器）" -ForegroundColor Yellow
    }

    Add-ReleaseRecord -RepoRoot $repoRoot -Record (New-ReleaseRecord -Action 'release' -Version $Version `
            -Color $targetColor -Result 'failed' -Stage $stage -Error $message -Previous $previousVersion `
            -Migrations $migrationFiles -Mode $mode)
    exit 1
}
