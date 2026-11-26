<#
.SYNOPSIS
  发布模式解析：bluegreen（蓝绿）与 inplace（单色原地重建）。

.DESCRIPTION
  模式来源优先级：命令行 -Mode > .env 的 RELEASE_MODE > bluegreen。
  归一化与校验只写一份，release.ps1 / rollback.ps1 共用，
  避免两个脚本对"什么算合法模式"各判一套。

  - bluegreen：两色并存，新色验证通过才切流，失败不伤线上（默认）；
  - inplace：只保留当前色，停旧起新，存在停机窗口，靠镜像 tag 回退。
#>

function Resolve-ReleaseMode {
    <#
    .SYNOPSIS
        把命令行与 .env 两种来源收敛成一个合法模式值。
    #>
    param(
        [string]$Requested = '',
        [string]$FromEnv = ''
    )

    $mode = if ([string]::IsNullOrWhiteSpace($Requested)) { $FromEnv } else { $Requested }
    if ([string]::IsNullOrWhiteSpace($mode)) { return 'bluegreen' }

    $mode = $mode.Trim().ToLowerInvariant()
    if ($mode -eq 'blue-green') { $mode = 'bluegreen' }
    if ($mode -notin @('bluegreen', 'inplace')) {
        throw "未知发布模式 '$mode'：可选 bluegreen（蓝绿）或 inplace（单色原地重建）"
    }
    return $mode
}

function Resolve-ReleaseTargetColor {
    <#
    .SYNOPSIS
        按模式决定本次要发布到哪个颜色。

    .DESCRIPTION
        蓝绿永远切到另一色；单色沿用当前色，首次发布（无状态）落到 blue。
        不能简单取反：首次发布与单色模式下取反会落到空的一色上。
    #>
    param(
        [string]$Mode = 'bluegreen',
        [string]$ActiveColor = 'none'
    )

    if ($Mode -eq 'inplace') {
        if ($ActiveColor -in @('blue', 'green')) { return $ActiveColor }
        return 'blue'
    }

    if ($ActiveColor -eq 'blue') { return 'green' }
    return 'blue'
}
