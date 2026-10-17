param(
    [Parameter(Mandatory=$true)][string]$Directory,
    [string]$Python = "python",
    [Parameter(Mandatory=$true)][string[]]$Command
)
$ErrorActionPreference = "Stop"
& $Python (Join-Path $PSScriptRoot "capture.py") --directory $Directory -- @Command
$producerStatus = $LASTEXITCODE
exit $producerStatus
