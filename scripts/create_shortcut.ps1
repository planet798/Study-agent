#Requires -Version 5.1
<#
.SYNOPSIS
  创建（或安全更新）Study Agent 一键启动快捷方式：桌面 + 开始菜单。

.DESCRIPTION
  - 快捷方式目标：PowerShell.exe（隐藏窗口）运行 scripts\start_study_agent.ps1。
  - 项目根目录基于本脚本位置（scripts/ 的上一级）自动解析：
    项目移动后重跑本脚本即可，快捷方式自动指向新位置。
  - 已存在同名快捷方式时“更新”它，不会重复创建多个快捷方式。
  - 只写当前用户桌面 / 开始菜单，无需管理员权限。
  - 图标使用默认 PowerShell 图标（不下载第三方图标）。
#>
[CmdletBinding()]
param(
    # 只创建桌面快捷方式，跳过开始菜单入口
    [switch]$DesktopOnly,

    # 只打印将要创建/删除的路径，不实际写入（供自动化测试）
    [switch]$DryRun,

    # 删除桌面与开始菜单的快捷方式
    [switch]$Remove,

    # 测试用：覆盖桌面/开始菜单目标目录（缺省用当前用户真实目录）
    [string]$DesktopPath = '',
    [string]$StartMenuPath = ''
)

$ProjectRoot   = Split-Path -Parent $PSScriptRoot
$Launcher      = Join-Path $ProjectRoot 'scripts\start_study_agent.ps1'
$PowerShellExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "launcher not found: $Launcher（请确认 scripts\start_study_agent.ps1 存在）"
}

# 目标 / 参数 / 工作目录
$Target = $PowerShellExe
$Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Launcher
$WorkingDir = $ProjectRoot

if ($DesktopPath) {
    $Desktop = $DesktopPath
} else {
    $Desktop = [Environment]::GetFolderPath('Desktop')
}

if ($StartMenuPath) {
    $StartMenuDir = $StartMenuPath
} else {
    $StartMenuDir = Join-Path ([Environment]::GetFolderPath('Programs')) 'Study Agent'
}

$DesktopLink  = Join-Path $Desktop 'Study Agent.lnk'
$StartMenuLink = Join-Path $StartMenuDir 'Study Agent.lnk'

# ---------- 删除模式 ----------
if ($Remove) {
    $targets = @($DesktopLink)
    if (-not $DesktopOnly) { $targets += $StartMenuLink }
    foreach ($t in $targets) {
        if (Test-Path -LiteralPath $t) {
            Remove-Item -LiteralPath $t -Force
            Write-Output "Removed: $t"
        } else {
            Write-Output "Not found (skip): $t"
        }
    }
    if (-not $DesktopOnly -and (Test-Path -LiteralPath $StartMenuDir) -and
        -not (Get-ChildItem -LiteralPath $StartMenuDir -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $StartMenuDir -Force
    }
    exit 0
}

# ---------- 创建（更新）单个快捷方式 ----------
function New-StudyAgentShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Dir,
        [string]$Name = 'Study Agent'
    )
    $lnk = Join-Path $Dir "$Name.lnk"
    if ($DryRun) {
        Write-Output "DryRun: would (re)create $lnk"
        return $lnk
    }
    if (-not (Test-Path -LiteralPath $Dir)) {
        New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    }
    # CreateShortcut 对已存在的 .lnk 是“加载”，改后 Save 即原地更新 → 不重复创建
    $ws = New-Object -ComObject WScript.Shell
    $sc = $ws.CreateShortcut($lnk)
    $sc.TargetPath       = $Target
    $sc.Arguments        = $Arguments
    $sc.WorkingDirectory = $WorkingDir
    $sc.Description      = 'Study Agent 一键启动'
    $sc.IconLocation     = '{0},0' -f $PowerShellExe   # 默认 PowerShell 图标
    $sc.Save()
    Write-Output "Created/Updated: $lnk"
    return $lnk
}

Write-Output "Target: $Target"
Write-Output "Arguments: $Arguments"
Write-Output "WorkingDirectory: $WorkingDir"

$desktopLnk = New-StudyAgentShortcut -Dir $Desktop
Write-Output "Desktop shortcut: $desktopLnk"

if (-not $DesktopOnly) {
    $startMenuLnk = New-StudyAgentShortcut -Dir $StartMenuDir
    Write-Output "Start Menu shortcut: $startMenuLnk"
}

Write-Output 'Done.'
