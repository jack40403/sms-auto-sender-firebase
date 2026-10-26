param(
    [string]$Python = '.\.venv313\Scripts\python.exe',
    [string]$Iscc = (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
)

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$meta = Get-Content -LiteralPath 'app_meta.json' -Raw -Encoding UTF8 | ConvertFrom-Json
$clientVersion = Select-String -LiteralPath 'app_meta.json' -Pattern '"version":\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value }
if ($meta.version -ne $clientVersion) { throw "Version mismatch: metadata=$($meta.version), client=$clientVersion" }
$issVersion = Select-String -LiteralPath 'installer.iss' -Pattern '^#define AppVersion "([^\"]+)"' | ForEach-Object { $_.Matches[0].Groups[1].Value }
if ($meta.version -ne $issVersion) { throw "Version mismatch: metadata=$($meta.version), installer=$issVersion" }
if (-not (Test-Path -LiteralPath $Iscc)) { throw "Inno Setup 6 compiler not found: $Iscc" }

& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name SMSAutoSender --add-data 'app_meta.json;.' --collect-all customtkinter --hidden-import win32timezone --hidden-import pythoncom --hidden-import win32com.client sms_modern_gui.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed' }
& $Iscc installer.iss
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }

$artifact = Join-Path 'release' "SMSAutoSender-Setup-$($meta.version).exe"
if (-not (Test-Path -LiteralPath $artifact)) { throw "Installer missing: $artifact" }
Get-FileHash -LiteralPath $artifact -Algorithm SHA256 | Format-List

