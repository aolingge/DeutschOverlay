param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'Programs\DeutschOverlay'),
    [string]$Source = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if ([string]::IsNullOrWhiteSpace($Source)) {
    $source = Join-Path $projectRoot 'dist\DeutschOverlay'
} else {
    $source = (Resolve-Path -LiteralPath $Source).Path
}
$sourceExe = Join-Path $source 'DeutschOverlay.exe'
if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "The folder to install from does not contain DeutschOverlay.exe: $source"
}

function Get-PackageManifest([string]$Folder) {
    $base = [IO.Path]::GetFullPath($Folder).TrimEnd('\', '/')
    @(
        Get-ChildItem -LiteralPath $base -Recurse -File -Force |
            ForEach-Object {
                $relative = $_.FullName.Substring($base.Length + 1).Replace('\', '/')
                "$relative`t$($_.Length)`t$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)"
            } | Sort-Object
    )
}

$rootFull = [IO.Path]::GetFullPath($InstallRoot)
$version = (Get-Item -LiteralPath $sourceExe).VersionInfo.FileVersion
if ([string]::IsNullOrWhiteSpace($version)) { $version = 'local' }
$sourceModels = @(
    (Join-Path $source 'models\whisper-small\model.bin'),
    (Join-Path $source 'models\opus-en-de\model.bin'),
    (Join-Path $source 'models\opus-zh-de\model.bin')
)
foreach ($path in $sourceModels) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Source model is missing: $path" }
}
$sourceManifest = @(Get-PackageManifest $source)
$hasher = [Security.Cryptography.SHA256]::Create()
try {
    $manifestBytes = [Text.Encoding]::UTF8.GetBytes($sourceManifest -join "`n")
    $packageId = [BitConverter]::ToString($hasher.ComputeHash($manifestBytes)).Replace('-', '').Substring(0, 16)
} finally {
    $hasher.Dispose()
}
$releaseName = "$version-$packageId"
$final = Join-Path $rootFull $releaseName
$stage = "$final.installing"
$rootPrefix = $rootFull.TrimEnd('\') + '\'
foreach ($target in @($stage, $final)) {
    if (-not ([IO.Path]::GetFullPath($target)).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Installation must remain inside the selected install root.'
    }
}
$reuse = Test-Path -LiteralPath $final
$repairExisting = $false
if ($reuse) {
    $installedManifest = @(Get-PackageManifest $final)
    if (($sourceManifest -join "`n") -cne ($installedManifest -join "`n")) {
        Write-Warning 'Existing install failed the full package checksum; preparing a verified replacement.'
        $repairExisting = $true
        $reuse = $false
    }
}
if (-not $reuse) {
    if (Test-Path -LiteralPath $stage) {
        Write-Warning "Resuming the incomplete copy at $stage"
    } else {
        New-Item -ItemType Directory -Path $stage -Force | Out-Null
    }
    Copy-Item -Path (Join-Path $source '*') -Destination $stage -Recurse -Force
}
$candidate = if ($reuse) { $final } else { $stage }
$candidateExe = Join-Path $candidate 'DeutschOverlay.exe'
$required = @(
    $candidateExe,
    (Join-Path $candidate 'models\whisper-small\model.bin'),
    (Join-Path $candidate 'models\opus-en-de\model.bin'),
    (Join-Path $candidate 'models\opus-zh-de\model.bin'),
    (Join-Path $candidate '_internal\faster_whisper\assets\silero_vad_v6.onnx')
)
foreach ($path in $required) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Installed package is incomplete: $path" }
}
if (-not $reuse) {
    $candidateManifest = @(Get-PackageManifest $candidate)
    if (($sourceManifest -join "`n") -cne ($candidateManifest -join "`n")) {
        throw 'Copied package failed the full file checksum; installation was not activated.'
    }
}

$priorModels = $env:DEUTSCH_OVERLAY_MODELS
try {
    $env:DEUTSCH_OVERLAY_MODELS = Join-Path $candidate 'models'
    $modelCheck = Start-Process -FilePath $candidateExe -ArgumentList '--self-test' -Wait -PassThru -WindowStyle Hidden
} finally {
    $env:DEUTSCH_OVERLAY_MODELS = $priorModels
}
if ($modelCheck.ExitCode -ne 0) { throw "Installed model self-test failed: $($modelCheck.ExitCode)" }
if (-not $reuse) {
    $damaged = $null
    if ($repairExisting) {
        $damaged = "$final.damaged-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
        if (-not ([IO.Path]::GetFullPath($damaged)).StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw 'Repair path must remain inside the selected install root.'
        }
        Move-Item -LiteralPath $final -Destination $damaged
    }
    try {
        Move-Item -LiteralPath $stage -Destination $final
    } catch {
        if ($damaged -and -not (Test-Path -LiteralPath $final)) {
            Move-Item -LiteralPath $damaged -Destination $final
        }
        throw
    }
    if ($damaged) { Write-Warning "Damaged install was moved to $damaged after its replacement passed self-test." }
}
$installedExe = Join-Path $final 'DeutschOverlay.exe'
$report = Join-Path $env:TEMP 'deutsch-overlay-install-audio.txt'
$priorReport = $env:DEUTSCH_OVERLAY_SELF_TEST_REPORT
try {
    $env:DEUTSCH_OVERLAY_SELF_TEST_REPORT = $report
    try {
        $audioCheck = Start-Process -FilePath $installedExe -ArgumentList '--audio-self-test' -Wait -PassThru -WindowStyle Hidden
        if ($audioCheck.ExitCode -ne 0) {
            Write-Warning "Audio device is unavailable during installation (exit $($audioCheck.ExitCode)); the app will retry when it reconnects."
        }
    } catch {
        Write-Warning "Audio self-test could not start: $($_.Exception.Message)"
    }
} finally {
    $env:DEUTSCH_OVERLAY_SELF_TEST_REPORT = $priorReport
}

$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('DesktopDirectory')
$programs = [Environment]::GetFolderPath('Programs')
$startMenu = Join-Path $programs 'Deutsch Overlay'
New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
foreach ($shortcutPath in @((Join-Path $desktop 'Deutsch Overlay.lnk'), (Join-Path $startMenu 'Deutsch Overlay.lnk'))) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $installedExe
    $shortcut.WorkingDirectory = $final
    $shortcut.IconLocation = "$installedExe,0"
    $shortcut.Description = '德语实时字幕'
    $shortcut.Save()
}

try {
    $olderRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'DeutschOverlay.exe'" -ErrorAction Stop |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ne $installedExe })
    if ($olderRunning.Count -gt 0) {
        Write-Warning 'An older Deutsch Overlay process is still running. Exit it from the tray before opening the new shortcut.'
    }
} catch {
    Write-Warning 'Could not check whether an older Deutsch Overlay process is running.'
}
$olderInstalls = @(Get-ChildItem -LiteralPath $rootFull -Directory |
    Where-Object { $_.FullName -ne $final -and $_.Name -notlike '*.installing' })
if ($olderInstalls.Count -gt 0) {
    Write-Output "Older installed versions remain for manual cleanup: $($olderInstalls.FullName -join ', ')"
}

Write-Output "Installed: $installedExe"
if ($reuse) { Write-Output 'Identical installed package reused; no second model copy was made.' }
Write-Output "Desktop shortcut: $(Join-Path $desktop 'Deutsch Overlay.lnk')"
Write-Output "Start menu shortcut: $(Join-Path $startMenu 'Deutsch Overlay.lnk')"
