param(
    [string]$Archive = '',
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\DeutschOverlay'),
    [string]$ChecksumFile = '',
    [switch]$VerifyOnly,
    [switch]$KeepExtracted
)

# Installs the app from a downloaded release archive:
#   1. verify the archive against SHA256SUMS.txt,
#   2. unpack it into a temporary folder,
#   3. hand the unpacked folder to install-local.ps1, which copies it into the
#      user profile, proves every byte with a checksum, runs the model self-test
#      and creates the desktop and start menu shortcuts.
#
# Nothing here downloads anything or writes outside the chosen install root.
# Run it with:  powershell -ExecutionPolicy Bypass -File .\install-release.ps1

$ErrorActionPreference = 'Stop'

function Resolve-ReleaseArchive {
    if (-not [string]::IsNullOrWhiteSpace($Archive)) {
        if (-not (Test-Path -LiteralPath $Archive -PathType Leaf)) {
            throw "The archive does not exist: $Archive"
        }
        return (Resolve-Path -LiteralPath $Archive).Path
    }
    $folders = @($PSScriptRoot, (Get-Location).Path, (Join-Path $env:USERPROFILE 'Downloads'))
    $found = @()
    foreach ($folder in $folders) {
        if (-not (Test-Path -LiteralPath $folder)) { continue }
        $found += @(Get-ChildItem -LiteralPath $folder -File -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'DeutschOverlay-*.7z' -or $_.Name -like 'DeutschOverlay-*.zip' })
    }
    $found = @($found | Sort-Object FullName -Unique)
    if ($found.Count -eq 0) {
        throw 'No DeutschOverlay-*.7z or DeutschOverlay-*.zip was found here, in the current folder or in Downloads. Pass -Archive with its full path.'
    }
    if ($found.Count -gt 1) {
        throw "Several release archives were found. Pass -Archive with the one to install: $($found.FullName -join ', ')"
    }
    return $found[0].FullName
}

function Assert-ArchiveChecksum([string]$Path, [string]$ChecksumPath) {
    if (-not $ChecksumPath) {
        $candidate = Join-Path (Split-Path -Parent $Path) 'SHA256SUMS.txt'
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { $ChecksumPath = $candidate }
    }
    if (-not $ChecksumPath) {
        Write-Warning 'SHA256SUMS.txt was not found next to the archive, so the download could not be verified. Download it from the release page to check the file before installing.'
        return $null
    }
    if (-not (Test-Path -LiteralPath $ChecksumPath -PathType Leaf)) {
        throw "The checksum file does not exist: $ChecksumPath"
    }
    $name = Split-Path -Leaf $Path
    $expected = $null
    foreach ($line in Get-Content -LiteralPath $ChecksumPath) {
        if ($line -match '^\s*([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') {
            if ($matches[2].Trim() -ieq $name) { $expected = $matches[1].ToLowerInvariant(); break }
        }
    }
    if (-not $expected) {
        throw "The checksum file does not list $name. Verify the file by hand before installing."
    }
    $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) {
        throw "The archive failed its checksum. Expected $expected but the file is $actual. Download it again; do not install this copy."
    }
    return $actual
}

function Expand-ReleaseArchive([string]$Path, [string]$Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $tar = Join-Path $env:SystemRoot 'System32\tar.exe'
    if (Test-Path -LiteralPath $tar) {
        & $tar -xf $Path -C $Destination
        if ($LASTEXITCODE -eq 0) { return }
        Write-Warning "tar.exe could not unpack the archive (exit $LASTEXITCODE); trying 7-Zip."
    }
    $sevenZipCandidates = @((Join-Path $env:ProgramFiles '7-Zip\7z.exe'))
    if (${env:ProgramFiles(x86)}) { $sevenZipCandidates += (Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe') }
    $sevenZip = @($sevenZipCandidates | Where-Object { Test-Path -LiteralPath $_ }) | Select-Object -First 1
    if (-not $sevenZip) {
        $sevenZip = (Get-Command '7z.exe' -ErrorAction SilentlyContinue | Select-Object -First 1).Source
    }
    if (-not $sevenZip) {
        throw "No unpacker is available. Extract $Path by hand, then run: powershell -ExecutionPolicy Bypass -File .\install-local.ps1 -Source <the extracted folder>"
    }
    & $sevenZip x -y -bso0 -bsp0 "-o$Destination" $Path
    if ($LASTEXITCODE -ne 0) { throw "7-Zip failed to unpack the archive (exit $LASTEXITCODE)." }
}

function Find-PackageRoot([string]$Stage) {
    $direct = Join-Path $Stage 'DeutschOverlay.exe'
    if (Test-Path -LiteralPath $direct) { return $Stage }
    $nested = Join-Path $Stage 'DeutschOverlay\DeutschOverlay.exe'
    if (Test-Path -LiteralPath $nested) { return (Join-Path $Stage 'DeutschOverlay') }
    $found = @(Get-ChildItem -LiteralPath $Stage -Recurse -File -Filter 'DeutschOverlay.exe' -Depth 2 |
        Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'models') })
    if ($found.Count -eq 0) {
        throw 'The archive does not contain a complete DeutschOverlay folder.'
    }
    return $found[0].DirectoryName
}

$archivePath = Resolve-ReleaseArchive
$archiveName = Split-Path -Leaf $archivePath
Write-Output "Archive: $archivePath"
Write-Output ("Size: {0:N1} MB" -f ((Get-Item -LiteralPath $archivePath).Length / 1MB))
$verified = Assert-ArchiveChecksum $archivePath $ChecksumFile
if ($verified) { Write-Output "Checksum verified: $archiveName $verified" }
if ($VerifyOnly) {
    if ($verified) { Write-Output 'Verification only: the archive matches its checksum.' }
    else { Write-Output 'Verification only: no checksum file was available.' }
    exit 0
}

$installer = Join-Path $PSScriptRoot 'install-local.ps1'
if (-not (Test-Path -LiteralPath $installer)) {
    throw "install-local.ps1 is missing beside this script: $installer"
}

$stage = Join-Path $env:TEMP ("deutsch-overlay-extract-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$activated = $false
try {
    Write-Output "Unpacking into $stage"
    Expand-ReleaseArchive $archivePath $stage
    $packageRoot = Find-PackageRoot $stage
    $global:LASTEXITCODE = 0
    & $installer -Source $packageRoot -InstallRoot $InstallRoot
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "install-local.ps1 failed with exit code $LASTEXITCODE." }
    $activated = $true
} finally {
    if ($activated -and -not $KeepExtracted) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    } elseif (-not $activated) {
        Write-Warning "The unpacked copy was kept for inspection: $stage"
    }
}
Write-Output "Installed from $archiveName"
