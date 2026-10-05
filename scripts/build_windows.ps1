param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$IsccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw '版本必须为 X.Y.Z' }
if (-not [Environment]::Is64BitProcess) { throw '需要 Windows x64 Python/PowerShell' }
$Root = Split-Path $PSScriptRoot -Parent
Push-Location $Root
try {
    if (-not (Test-Path $IsccPath)) { throw '未找到 Inno Setup 6 编译器' }
    New-Item -ItemType Directory -Force -Path build/runtime | Out-Null
    $Node = (Get-Command node -ErrorAction Stop).Source
    $NodeVersion = & $Node --version
    if ($NodeVersion -ne 'v22.19.0') { throw '构建需要锁定的 Node.js 22.19.0' }
    Copy-Item $Node build/runtime/node.exe -Force
    Copy-Item (Join-Path (Split-Path $Node -Parent) 'LICENSE') build/runtime/LICENSE -Force
    [IO.File]::WriteAllText((Join-Path $Root 'app/version.py'), "VERSION = `"$Version`"`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText((Join-Path $Root 'build/version.txt'), $Version, [Text.UTF8Encoding]::new($false))
    & python scripts/generate_app_icon.py
    if ($LASTEXITCODE -ne 0) { throw '图标生成失败' }
    Push-Location oauth_bridge
    try {
        $BridgeNpmCli = Join-Path (Split-Path $Node -Parent) 'node_modules/npm/bin/npm-cli.js'
        & $Node $BridgeNpmCli ci --omit=dev
        if ($LASTEXITCODE -ne 0) { throw 'OAuth 依赖安装失败' }
    } finally { Pop-Location }
    & python scripts/collect_release_licenses.py
    if ($LASTEXITCODE -ne 0) { throw '许可收集失败' }
    & python -m PyInstaller --clean --noconfirm packaging/study-agent.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows 应用打包失败' }
    & python -m PyInstaller --clean --noconfirm packaging/study-agent-updater.spec
    if ($LASTEXITCODE -ne 0) { throw 'Windows 升级辅助程序打包失败' }
    Copy-Item dist/StudyAgentUpdater.exe dist/StudyAgent/StudyAgentUpdater.exe -Force
    & $IsccPath "/DAppVersion=$Version" packaging/installer.iss
    if ($LASTEXITCODE -ne 0) { throw '安装器编译失败' }
} finally { Pop-Location }
