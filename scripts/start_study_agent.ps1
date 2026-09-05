#Requires -Version 5.1
<#
.SYNOPSIS
  Study Agent Windows 一键启动器（桌面快捷方式 / 手动命令行均可用）。

.DESCRIPTION
  - 自动定位项目根目录：本脚本所在目录（scripts/）的上一级，
    不依赖当前 PowerShell 工作目录，也不硬编码磁盘路径。
  - 固定使用项目虚拟环境：.venv\Scripts\python.exe 启动 app\main.py。
    不使用 PATH 中的随机 Python，也不要求执行 Activate.ps1。
  - 生产启动不传 --date：使用系统真实日期。
  - 启动器（及快捷方式）以隐藏方式运行；不会弹出长期停留的 PowerShell
    黑窗口，Study Agent 自身 PySide6 窗口正常显示。
  - 失败时弹出 Windows 原生错误框，并记录到 data\logs\launcher.log。
  - 绝不输出 / 记录 DEEPSEEK_API_KEY 等环境变量。
#>
[CmdletBinding()]
param(
    # 仅开发/测试用：模拟“今天”，例如 -Date 2026-09-05；生产启动请留空
    [string]$Date = "",

    # 只校验路径与将要执行的命令（打印后退出），供诊断/自动化测试
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

# ---------- 定位项目根目录（基于脚本自身位置，与 CWD 无关） ----------
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath  = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$MainPy      = Join-Path $ProjectRoot 'app\main.py'
$LogDir      = Join-Path $ProjectRoot 'data\logs'
$LogFile     = Join-Path $LogDir 'launcher.log'

function Write-LauncherLog {
    param([string]$Message)
    try {
        if (-not (Test-Path -LiteralPath $LogDir)) {
            New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
        }
        $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
        # UTF-8 无 BOM 追加，避免每次追加写 BOM
        $utf8NoBom = [System.Text.UTF8Encoding]::new($false)
        [System.IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, $utf8NoBom)
    } catch {
        # 日志写入失败不影响主流程
    }
}

function Show-LauncherError {
    param([string]$Detail)
    Write-LauncherLog "ERROR: $Detail"
    if ($DryRun) {
        # 自动化测试 / 诊断模式：只打印错误，不弹窗、不阻塞
        Write-Output "ERROR: $Detail"
        return
    }
    try {
        Add-Type -AssemblyName PresentationFramework | Out-Null
        $msg = "Study Agent 启动失败：`n`n" +
               "项目目录：$ProjectRoot`n" +
               "Python 路径：$PythonPath`n`n" +
               "原因：$Detail`n`n详情已记录到：$LogFile"
        [System.Windows.MessageBox]::Show($msg, 'Study Agent', 'OK', 'Error') | Out-Null
    } catch {
        # 无交互桌面（极罕见）时退化为控制台错误输出
        Write-Output "ERROR: $Detail"
    }
}

# ---------- 必要文件检查 ----------
if (-not (Test-Path -LiteralPath $PythonPath)) {
    Show-LauncherError "未找到 $PythonPath"
    exit 1
}
if (-not (Test-Path -LiteralPath $MainPy)) {
    Show-LauncherError "未找到 $MainPy"
    exit 1
}

Write-LauncherLog "launch: python=$PythonPath main=$MainPy"

# ---------- 组装启动命令 ----------
$ArgumentList = @('app\main.py')
if ($Date) {
    $ArgumentList += '--date'
    $ArgumentList += $Date
}

if ($DryRun) {
    Write-Output "ProjectRoot=$ProjectRoot"
    Write-Output "Python=$PythonPath"
    Write-Output "MainPy=$MainPy"
    Write-Output ("Command: {0} {1}" -f $PythonPath, ($ArgumentList -join ' '))
    exit 0
}

# 设置工作目录为项目根目录（app\main.py 依赖相对路径）
Set-Location -LiteralPath $ProjectRoot

# 隐藏方式启动 python.exe（窗口隐藏的是启动控制台，不影响 Qt GUI）
$proc = Start-Process -FilePath $PythonPath `
    -ArgumentList $ArgumentList `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -PassThru

Write-LauncherLog "started pid=$($proc.Id)"

# 启动数秒内即失败（崩溃 / 导入错误等）时给出明确提示
Start-Sleep -Milliseconds 800
if ($proc.HasExited -and $proc.ExitCode -ne 0) {
    Show-LauncherError "Python 进程立即退出，退出码 $($proc.ExitCode)"
    exit 1
}

exit 0
