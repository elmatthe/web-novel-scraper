<#
Installs Python 3.12 for the current user when the launcher finds no usable Python (v0.3.0 07).

This step cannot be Python: it is what provides Python. It downloads the official python.org
installer into files\runtime-data\bootstrap, refuses it unless both its SHA-256 and its Python
Software Foundation signature check out, runs it for this user only (no admin, PATH unchanged)
and deletes it. The launcher then looks for Python again and runs it; the installer's own exit
code is never taken as proof that Python works.

The pinned installer and hash are the same ones PDF-Splitter uses (3.12.10 is the last 3.12
release with a Windows installer); the hash was re-checked against a fresh download on 2026-10-18.

Exit codes: 0 installer ran, 2 download failed, 3 verification failed, 4 installer failed.
-VerifyOnly downloads and verifies without installing (used to test this script).
#>
param([switch]$VerifyOnly)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Windows PowerShell's progress bar slows downloads badly

$Url = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'
$Sha256 = '67B5635E80EA51072B87941312D00EC8927C4DB9BA18938F7AD2D27B328B95FB'
$Signer = 'Python Software Foundation'

$root = Split-Path -Parent $PSScriptRoot
$folder = Join-Path $root 'files\runtime-data\bootstrap'
$installer = Join-Path $folder 'python-3.12.10-amd64.exe'
New-Item -ItemType Directory -Force -Path $folder | Out-Null

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Write-Host '  Downloading the Python 3.12.10 installer from python.org (about 26 MB)...'
    Invoke-WebRequest -Uri $Url -OutFile $installer -UseBasicParsing
} catch {
    Write-Host "  The download failed: $($_.Exception.Message)"
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    exit 2
}

$hash, $status, $subject = 'unknown', 'unknown', ''
try {
    # .NET directly: Get-FileHash needs a module that fails to load when PSModulePath was set by
    # PowerShell 7 (the launcher also clears it before starting this script).
    $stream = [IO.File]::OpenRead($installer)
    try { $digest = [Security.Cryptography.SHA256]::Create().ComputeHash($stream) } finally { $stream.Dispose() }
    $hash = -join ($digest | ForEach-Object { $_.ToString('X2') })
    $signature = Get-AuthenticodeSignature -LiteralPath $installer
    $status = $signature.Status
    if ($signature.SignerCertificate) { $subject = $signature.SignerCertificate.Subject }
} catch {
    $status = "not checked ($($_.Exception.Message))"
}
if ($hash -ne $Sha256 -or $status -ne 'Valid' -or $subject -notlike "*$Signer*") {
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    Write-Host "  The downloaded installer did not pass verification (SHA-256 $hash, signature"
    Write-Host "  $status), so it was deleted without being run."
    exit 3
}
Write-Host '  Installer verified (SHA-256 and Python Software Foundation signature).'
if ($VerifyOnly) {
    Remove-Item -LiteralPath $installer -Force
    exit 0
}

Write-Host '  Installing Python 3.12 for your user only (no admin needed)...'
$arguments = '/passive', 'InstallAllUsers=0', 'PrependPath=0', 'Include_launcher=0', 'Include_test=0',
             'Include_pip=1', 'Include_tcltk=1'
$process = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
if ($process.ExitCode -ne 0) {
    Write-Host "  The Python installer ended with code $($process.ExitCode)."
    exit 4
}
exit 0
