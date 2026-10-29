[CmdletBinding()]
param(
    [switch]$SkipInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$TargetSpec = "tests/08-uiux-s18-content-readability.spec.ts"

Write-Output "=== OPENAIR UIUX HARNESS S18 CONTENT READABILITY RUNNER ==="
Write-Output "TARGET_SPEC=$TargetSpec"
Write-Output "FULL_BASELINE=NOT_RUN"
Write-Output "PRODUCT_SOURCE_MODIFIED=NO"

$BaselineScript = Join-Path $PSScriptRoot "run-baseline.ps1"
if (-not (Test-Path -LiteralPath $BaselineScript)) {
    throw "Missing baseline runner: $BaselineScript"
}

if ($SkipInstall) {
    & $BaselineScript -SkipInstall -TestSpec $TargetSpec
}
else {
    & $BaselineScript -TestSpec $TargetSpec
}

$ExitCode = $LASTEXITCODE
Write-Output "PLAYWRIGHT_EXIT_CODE=$ExitCode"
Write-Output "NOTE: Non-zero Playwright exit code may indicate S18 readability gate findings."

exit $ExitCode
