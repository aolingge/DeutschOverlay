param(
    [switch]$SkipModels,
    [switch]$NoBuild
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$distRoot = Join-Path $projectRoot 'dist'
$appDir = Join-Path $distRoot 'DeutschOverlay'
$expectedPrefix = $projectRoot.TrimEnd('\') + '\'
if (-not $appDir.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must remain inside the project workspace.'
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Install the project in .venv first.' }

if (-not $NoBuild) {
    $entry = Join-Path $projectRoot 'src\deutsch_overlay\app.py'
    $arguments = @(
        '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
        '--name', 'DeutschOverlay', '--paths', (Join-Path $projectRoot 'src'),
        '--version-file', (Join-Path $PSScriptRoot 'version_info.txt'),
        '--collect-data', 'faster_whisper',
        '--collect-submodules', 'transformers.models.marian',
        '--collect-all', 'azure.cognitiveservices.speech',
        '--collect-all', 'nvidia.cudnn',
        '--collect-all', 'nvidia.cublas',
        '--collect-all', 'nvidia.cuda_nvrtc',
        '--hidden-import', 'keyring.backends.Windows',
        '--hidden-import', 'soundcard.mediafoundation',
        '--distpath', $distRoot,
        '--workpath', (Join-Path $projectRoot 'build'),
        $entry
    )
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
}

$exe = Join-Path $appDir 'DeutschOverlay.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'DeutschOverlay.exe was not produced.' }
$vadAsset = Join-Path $appDir '_internal\faster_whisper\assets\silero_vad_v6.onnx'
if (-not (Test-Path -LiteralPath $vadAsset)) { throw 'Speech detection model is missing from the EXE bundle.' }
$basePrefix = (& $python -c 'import sys; print(sys.base_prefix)').Trim()
$condaDlls = Join-Path $basePrefix 'Library\bin'
foreach ($name in @('ffi.dll', 'libexpat.dll', 'libcrypto-3-x64.dll', 'libssl-3-x64.dll')) {
    $source = Join-Path $condaDlls $name
    if (Test-Path -LiteralPath $source) {
        Copy-Item -LiteralPath $source -Destination (Join-Path $appDir "_internal\$name") -Force
    }
}
# The distributed folder must carry the user documentation plus the licence and
# third-party notices: the package contains LGPL-3.0 Qt, CUDA runtime libraries
# and three openly licensed models, so the notices have to travel with it.
foreach ($document in @(
        'README.md',
        'MODEL_SOURCES.md',
        'LICENSE',
        'THIRD-PARTY-NOTICES.md',
        'SECURITY.md',
        'CHANGELOG.md'
    )) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $document) -Destination $appDir -Force
}

if (-not $SkipModels) {
    $models = Join-Path $projectRoot 'models'
    & $python (Join-Path $PSScriptRoot 'check_models.py') $models
    if ($LASTEXITCODE -ne 0) { throw 'The local model set is incomplete.' }
    $appModels = Join-Path $appDir 'models'
    New-Item -ItemType Directory -Path $appModels -Force | Out-Null
    foreach ($name in @('whisper-small', 'opus-en-de', 'opus-zh-de')) {
        Copy-Item -LiteralPath (Join-Path $models $name) -Destination $appModels -Recurse -Force
    }
}

Write-Output "Built: $exe"
if ($SkipModels) { Write-Output 'Models were skipped; this is a build check only.' }
