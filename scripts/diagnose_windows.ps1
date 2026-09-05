#Requires -Version 5.1
<#
.SYNOPSIS
  Study Agent Windows 启动诊断脚本。

.DESCRIPTION
  输出：项目根目录、Python 路径、Python 版本、PySide6 版本、
  DeepSeek API 配置状态。

  安全约定：DEEPSEEK_API_KEY 只显示 Configured / Missing，
  绝对不打印 API Key 本身。
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonPath  = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

Write-Output "Project root:  $ProjectRoot"
Write-Output "Python path:   $PythonPath"
Write-Output "Python exists: $(Test-Path -LiteralPath $PythonPath)"
Write-Output "main.py exists: $(Test-Path -LiteralPath (Join-Path $ProjectRoot 'app\main.py'))"

if (Test-Path -LiteralPath $PythonPath) {
    try {
        $pyver = & $PythonPath -V 2>&1
        Write-Output "Python version: $pyver"
    } catch {
        Write-Output "Python version: (failed to run)"
    }
    try {
        $pyside = & $PythonPath -c "import PySide6; print(PySide6.__version__)" 2>&1
        Write-Output "PySide6 version: $pyside"
    } catch {
        Write-Output "PySide6 version: (failed to run)"
    }
}

# ---------- DeepSeek API 配置状态（只显示状态，绝不输出 Key） ----------
function Get-UserEnv {
    param([string]$Name)
    $v = [Environment]::GetEnvironmentVariable($Name, 'User')
    if (-not $v) { $v = [Environment]::GetEnvironmentVariable($Name, 'Process') }
    return $v
}

$apiKey = Get-UserEnv 'DEEPSEEK_API_KEY'
$baseUrl = Get-UserEnv 'DEEPSEEK_BASE_URL'
$model = Get-UserEnv 'DEEPSEEK_MODEL'

# 注意：此处仅把 $apiKey 当布尔值使用，绝不输出其内容
if ($apiKey) {
    Write-Output "DEEPSEEK_API_KEY: Configured"
} else {
    Write-Output "DEEPSEEK_API_KEY: Missing"
}
# BASE_URL / MODEL 不是密钥，可以显示
Write-Output "DEEPSEEK_BASE_URL: $baseUrl"
Write-Output "DEEPSEEK_MODEL: $model"
