[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$TestSpec,
    [string]$ApiBaseUrl = $(if ($env:OPENAIR_PRODUCT_HARNESS_API_BASE_URL) { $env:OPENAIR_PRODUCT_HARNESS_API_BASE_URL } else { "http://127.0.0.1:8000" }),
    [string]$WebBaseUrl = $(if ($env:OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL) { $env:OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL } else { "http://127.0.0.1:8080" })
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$HarnessRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunId = Get-Date -Format "yyyyMMdd-HHmmss"
$RunDir = Join-Path ([System.IO.Path]::GetTempPath()) "openair-product-harness-runs\$RunId"
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null

try {
    $Diagnostics = Invoke-RestMethod -Uri "$ApiBaseUrl/health/diagnostics" -TimeoutSec 10
    if ($Diagnostics.persistenceBackend -ne "postgres") {
        throw "HARNESS_ENVIRONMENT_CONTRACT_INVALID: production stack must report persistenceBackend=postgres; observed persistenceBackend=$($Diagnostics.persistenceBackend)."
    }
    $Web = Invoke-WebRequest -UseBasicParsing -Uri $WebBaseUrl -TimeoutSec 10
    if ($Web.StatusCode -lt 200 -or $Web.StatusCode -ge 400) { throw "HARNESS_WEB_UNAVAILABLE: $WebBaseUrl returned HTTP $($Web.StatusCode)." }
    $env:OPENAIR_PRODUCT_HARNESS_RUN_ID = $RunId
    $env:OPENAIR_PRODUCT_HARNESS_RUN_DIR = $RunDir
    $env:OPENAIR_PRODUCT_HARNESS_API_BASE_URL = $ApiBaseUrl
    $env:OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL = $WebBaseUrl
    $env:OPENAIR_PRODUCT_HARNESS_AUTH_REQUIRED = "1"
    Write-Output "HARNESS_MODE=production-stack"
    # Compose runs durable-worker as a separate service while the API may use
    # runtimeType=inline for workunit dispatch; persistence is the API-visible
    # contract we can verify without reaching into Docker implementation state.
    Write-Output "HARNESS_ENVIRONMENT_CONTRACT=postgres"
    Push-Location $HarnessRoot
    try { & npx.cmd playwright test --project=chromium-desktop $TestSpec.Replace("\", "/"); exit $LASTEXITCODE } finally { Pop-Location }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
