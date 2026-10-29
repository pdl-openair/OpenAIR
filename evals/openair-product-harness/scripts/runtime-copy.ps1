function Copy-DirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Required source directory not found: $Source"
    }

    $Parent = Split-Path -Parent $Destination
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $Destination -Recurse -Force
}

function Copy-OptionalDirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][string]$Label
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        Write-Output "SKIP: optional runtime directory '$Label' is absent and is not required for this run."
        return
    }

    Copy-DirectoryTree -Source $Source -Destination $Destination
    Write-Output "PASS: optional runtime directory '$Label' copied into the isolated runtime."
}

function Assert-IsolatedSpecSupported {
    param([AllowEmptyString()][string]$TestSpec)

    # JSON isolated mode is fixture/replay-only. These browser families exercise
    # durable Research Start or worker-backed product paths whose authority is PostgreSQL.
    $Normalized = $TestSpec.Replace("\", "/").ToLowerInvariant()
    $Unsupported = @(
        "11-research-start-", "13-research-start-", "20-home-question-refinement-live",
        "23-representative-research-inputs", "25-live-agent-audit", "26-huiyan-",
        "27-huiyan-", "28-huiyan-", "29-huiyan-", "30-start-without-implicit-execution",
        "31-explicit-single-skill-live", "32-huiyan-", "33-real-literature-reliability-flow",
        "34-automatic-research-completion", "35-t10-durable-multiagent-sse",
        "36-c3-source-authenticity-mainline", "37-ot-p0f-golden-path"
    )
    if ($Unsupported | Where-Object { $Normalized.Contains($_) }) {
        throw "HARNESS_ENVIRONMENT_CONTRACT_INVALID: '$TestSpec' requires production-stack mode (PostgreSQL/durable runtime); isolated JSON replay mode is not supported. Use scripts/run-production-stack.ps1."
    }
}
