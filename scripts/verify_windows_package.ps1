param([Parameter(Mandatory = $true)][string]$Version)
$ErrorActionPreference = 'Stop'
$Root = Split-Path $PSScriptRoot -Parent
$Installer = Join-Path $Root "dist/installer/StudyAgent-Setup-$Version-x64.exe"
$Work = Join-Path $env:RUNNER_TEMP ('StudyAgent 验证 ' + [Guid]::NewGuid())
$Install = Join-Path $Work '自选 安装目录'
$Report = Join-Path $Work 'self-test.json'
$DataHome = Join-Path $Work '用户数据'
$OriginalLocalAppData = $env:LOCALAPPDATA
function Invoke-Checked([string]$File, [string[]]$Arguments) {
    $Process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    if (-not $Process.WaitForExit(180000)) { $Process.Kill(); throw '验证进程超时' }
    if ($Process.ExitCode -ne 0) { throw "验证进程失败：$($Process.ExitCode)" }
}
try {
    New-Item -ItemType Directory -Force -Path $Work,$DataHome | Out-Null
    # 安装器覆盖路径与应用数据路径分别验证。
    $env:LOCALAPPDATA = $DataHome
    Invoke-Checked $Installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=desktopicon', "/DIR=`"$Install`"")
    $Exe = Join-Path $Install 'StudyAgent.exe'
    if (-not (Test-Path $Exe)) { throw '安装后主程序缺失' }
    # 让 PATH 中找不到 Python/Node，冻结程序必须使用随包运行时。
    $OriginalPath = $env:PATH
    try {
        $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
        Invoke-Checked $Exe @('--self-test', "`"$Report`"")
    } finally { $env:PATH = $OriginalPath }
    $Result = Get-Content -LiteralPath $Report -Raw | ConvertFrom-Json
    if (-not $Result.ok -or $Result.version -ne $Version) { throw '冻结程序自检失败' }
    $Sentinel = Join-Path $DataHome 'StudyAgent/data/upgrade-sentinel.txt'
    New-Item -ItemType Directory -Force -Path (Split-Path $Sentinel -Parent) | Out-Null
    [IO.File]::WriteAllText($Sentinel, 'preserve-user-data')
    Invoke-Checked $Installer @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=`"$Install`"")
    if ((Get-Content -LiteralPath $Sentinel -Raw) -ne 'preserve-user-data') { throw '覆盖安装破坏了用户数据' }
    Invoke-Checked (Join-Path $Install 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if (-not (Test-Path $Sentinel)) { throw '卸载删除了用户数据' }
    if (Test-Path $Exe) { throw '卸载未移除主程序' }
    Copy-Item $Report (Join-Path $Root 'dist/installer/self-test.json')
} finally {
    $StartupLog = Join-Path $DataHome 'StudyAgent/logs/startup.log'
    if (Test-Path $StartupLog) { Copy-Item $StartupLog (Join-Path $Root 'dist/installer/startup.log') }
    if (Test-Path $Report) { Copy-Item $Report (Join-Path $Root 'dist/installer/self-test.json') -Force }
    $env:LOCALAPPDATA = $OriginalLocalAppData
    Remove-Item -LiteralPath $Work -Recurse -Force -ErrorAction SilentlyContinue
}
