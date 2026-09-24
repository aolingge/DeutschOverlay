param(
    [ValidateSet('all', 'whisper-small', 'opus-en-de', 'opus-zh-de')]
    [string]$Model = 'all'
)

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$converterPython = Join-Path $projectRoot '.model-venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $converterPython)) {
    throw 'Create .model-venv with system site packages and install ctranslate2, transformers, sentencepiece, and huggingface-hub first.'
}
$script = Join-Path $PSScriptRoot 'prepare_models.py'
$destination = Join-Path $projectRoot 'models'
& $converterPython $script --root $destination --model $Model
if ($LASTEXITCODE -ne 0) { throw "Model preparation failed with exit code $LASTEXITCODE" }
