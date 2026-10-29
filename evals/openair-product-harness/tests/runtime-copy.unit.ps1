[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "..\scripts\runtime-copy.ps1")

function Assert-True {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw "ASSERTION FAILED: $Message"
    }
}

$TestRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("openair-runtime-copy-test-" + [guid]::NewGuid().ToString("N"))

try {
    New-Item -ItemType Directory -Path $TestRoot | Out-Null

    $StableSource = Join-Path $TestRoot "stable-source"
    $StableDestination = Join-Path $TestRoot "runtime\stable-source"
    $EphemeralClaude = Join-Path $TestRoot ".claude\temp_audit_worktree"
    New-Item -ItemType Directory -Path $StableSource -Force | Out-Null
    New-Item -ItemType Directory -Path $EphemeralClaude -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $StableSource "runtime.json") -Value "{}" -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $EphemeralClaude "replay.json") -Value "{}" -Encoding ASCII
    Copy-DirectoryTree -Source $StableSource -Destination $StableDestination
    Assert-True -Condition (Test-Path -LiteralPath (Join-Path $StableDestination "runtime.json") -PathType Leaf) -Message "stable runtime source should copy when an adjacent .claude audit tree exists"
    Assert-True -Condition (-not (Test-Path -LiteralPath (Join-Path $TestRoot "runtime\.claude"))) -Message "runtime-copy allowlist must not copy adjacent .claude or audit trees"

    $UnsupportedFailure = $null
    try { Assert-IsolatedSpecSupported -TestSpec "tests/11-research-start-draft-workbench.spec.ts" } catch { $UnsupportedFailure = $_.Exception.Message }
    Assert-True -Condition ($UnsupportedFailure -like "HARNESS_ENVIRONMENT_CONTRACT_INVALID:*") -Message "Research Start must fail before JSON isolated API startup with an environment-contract error"

    $RequiredFailure = $null
    try {
        Copy-DirectoryTree -Source (Join-Path $TestRoot "missing") -Destination (Join-Path $TestRoot "missing-output")
    }
    catch {
        $RequiredFailure = $_.Exception.Message
    }
    Assert-True `
        -Condition ($RequiredFailure -like "Required source directory not found:*") `
        -Message "a genuinely required source directory should still fail clearly"

    Write-Output "PASS: runtime copy unit tests (3 cases)"
}
finally {
    if (Test-Path -LiteralPath $TestRoot) {
        Remove-Item -LiteralPath $TestRoot -Recurse -Force
    }
}
