[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [string]$TestSpec = "",
    [switch]$LiveRun,
    [switch]$HuiyanPlannerLive,
    [switch]$HuiyanSkillsLive
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "runtime-copy.ps1")

function Resolve-ApplicationPath {
    param([Parameter(Mandatory = $true)][string[]]$Names)

    foreach ($Name in $Names) {
        $Command = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($null -ne $Command) {
            return [string]$Command.Source
        }
    }

    return $null
}

function Assert-ApplicationPath {
    param(
        [Parameter(Mandatory = $true)][string[]]$Names,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $Path = Resolve-ApplicationPath -Names $Names
    if ([string]::IsNullOrWhiteSpace($Path)) {
        throw "Required command not found: $Label"
    }

    return $Path
}

function Test-HttpOk {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $Response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
        return ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 300)
    }
    catch {
        return $false
    }
}

function Wait-HttpOk {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        if (Test-HttpOk -Url $Url) {
            Write-Output "PASS: $Label is ready at $Url"
            return
        }
        Start-Sleep -Milliseconds 600
    }

    throw "$Label did not become ready within $TimeoutSeconds seconds: $Url"
}

function Test-LoopbackTcpPortAvailable {
    param([Parameter(Mandatory = $true)][int]$Port)

    $Listener = $null
    try {
        $Address = [System.Net.IPAddress]::Parse("127.0.0.1")
        $Listener = [System.Net.Sockets.TcpListener]::new($Address, $Port)
        $Listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $Listener) {
            try { $Listener.Stop() } catch { }
        }
    }
}

function Select-FreeLoopbackPort {
    param(
        [Parameter(Mandatory = $true)][int[]]$Candidates,
        [Parameter(Mandatory = $true)][string]$Label
    )

    foreach ($Candidate in $Candidates) {
        if (Test-LoopbackTcpPortAvailable -Port $Candidate) {
            return [int]$Candidate
        }
    }

    throw "No free loopback port found for $Label. Candidates=$($Candidates -join ',')"
}

function Copy-FileToDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$DestinationDirectory
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Required source file not found: $Source"
    }

    New-Item -ItemType Directory -Force -Path $DestinationDirectory | Out-Null
    Copy-Item -LiteralPath $Source -Destination $DestinationDirectory -Force
}

function Import-EnvironmentKeysFromFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Names
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required environment file not found: $Path"
    }

    foreach ($Line in Get-Content -LiteralPath $Path) {
        if ($Line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$') {
            continue
        }
        $Name = $matches[1]
        if ($Names -notcontains $Name -or -not [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($Name, 'Process'))) {
            continue
        }
        $Value = $matches[2].Trim()
        if ($Value.Length -ge 2 -and (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or ($Value.StartsWith("'") -and $Value.EndsWith("'")))) {
            $Value = $Value.Substring(1, $Value.Length - 2)
        }
        if (-not [string]::IsNullOrWhiteSpace($Value)) {
            [Environment]::SetEnvironmentVariable($Name, $Value, 'Process')
        }
    }
}

function Resolve-ApiLauncher {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $VenvPython = Join-Path $RepositoryRoot "services\api\.venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $VenvPython) {
        return [pscustomobject]@{
            FilePath = $VenvPython
            PrefixArguments = [string[]]@()
            Label = "services/api/.venv/Scripts/python.exe"
        }
    }

    $UvPath = Resolve-ApplicationPath -Names @("uv.exe", "uv")
    if (-not [string]::IsNullOrWhiteSpace($UvPath)) {
        return [pscustomobject]@{
            FilePath = $UvPath
            PrefixArguments = [string[]]@("run", "python")
            Label = "uv run python"
        }
    }

    $PythonPath = Resolve-ApplicationPath -Names @("python.exe", "python")
    if (-not [string]::IsNullOrWhiteSpace($PythonPath)) {
        return [pscustomobject]@{
            FilePath = $PythonPath
            PrefixArguments = [string[]]@()
            Label = "python"
        }
    }

    $PyPath = Resolve-ApplicationPath -Names @("py.exe", "py")
    if (-not [string]::IsNullOrWhiteSpace($PyPath)) {
        return [pscustomobject]@{
            FilePath = $PyPath
            PrefixArguments = [string[]]@("-3")
            Label = "py -3"
        }
    }

    throw "Required API launcher not found. Expected services/api/.venv, uv, python, or py -3."
}

function Start-LoggedPowerShellFile {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptPath,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    return Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath `
        -PassThru
}

function Write-OptionalLogTail {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Write-Output "--- $Label ---"
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Get-Content -LiteralPath $Path -Tail 120 -ErrorAction SilentlyContinue
    }
    else {
        Write-Output "LOG_NOT_FOUND=$Path"
    }
}

function Wait-HttpOkOrProcessExit {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    $Deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $Deadline) {
        if (Test-HttpOk -Url $Url) {
            Write-Output "PASS: $Label is ready at $Url"
            return
        }

        if ($Process.HasExited) {
            Write-OptionalLogTail -Path $StdoutPath -Label "$Label STDOUT"
            Write-OptionalLogTail -Path $StderrPath -Label "$Label STDERR"
            throw "$Label process exited before readiness. EXIT_CODE=$($Process.ExitCode)"
        }

        Start-Sleep -Milliseconds 600
    }

    Write-OptionalLogTail -Path $StdoutPath -Label "$Label STDOUT"
    Write-OptionalLogTail -Path $StderrPath -Label "$Label STDERR"
    throw "$Label did not become ready within $TimeoutSeconds seconds: $Url"
}

function Stop-ProcessSafe {
    param([AllowNull()][System.Diagnostics.Process]$Process)

    if ($null -eq $Process) { return }

    try {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        Write-Warning "Unable to stop owned process PID=$($Process.Id): $($_.Exception.Message)"
    }
}

function Stop-IsolatedPortListenerSafe {
    param(
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$Label
    )

    try {
        $Listeners = @(
            Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
        )

        foreach ($Listener in $Listeners) {
            $PidValue = [int]$Listener.OwningProcess
            if ($PidValue -gt 0) {
                Write-Output "CLEANUP_ISOLATED_LISTENER=$Label PORT=$Port PID=$PidValue"
                Stop-Process -Id $PidValue -Force -ErrorAction SilentlyContinue
            }
        }
    }
    catch {
        Write-Warning "Unable to clean isolated listener for $Label on port ${Port}: $($_.Exception.Message)"
    }
}

$HarnessRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RepoRoot = (Resolve-Path (Join-Path $HarnessRoot "..\..")).Path
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$RunRoot = Join-Path $HOME "Downloads\openair-product-harness-runs"
$RunDir = Join-Path $RunRoot $Timestamp
$ServerLogDir = Join-Path $RunDir "server-logs"
$IsolatedRepoRoot = Join-Path $RunDir "api"
$IsolatedApiRoot = Join-Path $IsolatedRepoRoot "services\api"
$ApiProcess = $null
$WebProcess = $null
$ExitCode = 1
$HarnessExecution = "FAIL"
$ObservedProductOutcome = "NOT_OBSERVED"
$ApiPort = Select-FreeLoopbackPort -Candidates ([int[]](18000..18009)) -Label "isolated OpenAIR API"
$WebPort = Select-FreeLoopbackPort -Candidates ([int[]](15173..15182)) -Label "OpenAIR web"
$ApiBaseUrl = "http://127.0.0.1:$ApiPort"
$ApiHealthUrl = "$ApiBaseUrl/health"
$WebBaseUrl = "http://127.0.0.1:$WebPort"
$WebBoardUrl = "$WebBaseUrl/workbench"

New-Item -ItemType Directory -Force -Path $ServerLogDir | Out-Null

$env:OPENAIR_PRODUCT_HARNESS_RUN_ID = $Timestamp
$env:OPENAIR_PRODUCT_HARNESS_RUN_DIR = $RunDir
$env:OPENAIR_PRODUCT_HARNESS_API_BASE_URL = $ApiBaseUrl
$env:OPENAIR_PRODUCT_HARNESS_WEB_BASE_URL = $WebBaseUrl
$env:OPENAIR_PRODUCT_REQUIRE_LIVE = if ($HuiyanSkillsLive) { "1" } else { "0" }

Write-Output "=== OPENAIR PRODUCT HARNESS P2.1 ISOLATED BASELINE ==="
Write-Output "REPO_ROOT=$RepoRoot"
Write-Output "HARNESS_ROOT=$HarnessRoot"
Write-Output "RUN_DIR=$RunDir"
Write-Output "ISOLATED_RUNTIME_DIR=$IsolatedRepoRoot"
Write-Output "API_BASE_URL=$ApiBaseUrl"
Write-Output "WEB_BASE_URL=$WebBaseUrl"
Write-Output "IMPORTANT: existing services on ports 8000 and 5173 are not reused, stopped, or mutated."
Write-Output "IMPORTANT: repository data/tasks is never moved or modified by this runner."

try {
    Assert-IsolatedSpecSupported -TestSpec $TestSpec
    if ($LiveRun -or $HuiyanPlannerLive -or $HuiyanSkillsLive) {
        throw "HARNESS_ENVIRONMENT_CONTRACT_INVALID: live provider, durable, and production Skill runs require production-stack mode. Use scripts/run-production-stack.ps1 with an explicit -TestSpec."
    }
    if (($LiveRun -or $HuiyanPlannerLive -or $HuiyanSkillsLive) -and [string]::IsNullOrWhiteSpace($TestSpec)) {
        throw "-LiveRun, -HuiyanPlannerLive, and -HuiyanSkillsLive require -TestSpec so real model calls remain bounded to one reviewed scenario."
    }
    if ($LiveRun -or $HuiyanPlannerLive -or $HuiyanSkillsLive) {
        Import-EnvironmentKeysFromFile `
            -Path (Join-Path $RepoRoot ".env") `
            -Names @("HUIYAN_API_KEY", "HUIYAN_API_BASE_URL", "HUIYAN_LLM_MODEL")
        foreach ($RequiredName in @("HUIYAN_API_KEY", "HUIYAN_API_BASE_URL", "HUIYAN_LLM_MODEL")) {
            if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($RequiredName, 'Process'))) {
                throw "Live HUIYAN execution requires configured $RequiredName in the process environment or repository .env."
            }
        }
    }
    $NodePath = Assert-ApplicationPath -Names @("node.exe", "node") -Label "node"
    $NpmPath = Assert-ApplicationPath -Names @("npm.cmd", "npm") -Label "npm"
    $NpxPath = Assert-ApplicationPath -Names @("npx.cmd", "npx") -Label "npx"
    $PnpmPath = Assert-ApplicationPath -Names @("pnpm.cmd", "pnpm") -Label "pnpm"
    $ApiLauncher = Resolve-ApiLauncher -RepositoryRoot $RepoRoot

    Write-Output "NODE=$NodePath"
    Write-Output "API_LAUNCHER=$($ApiLauncher.Label)"

    if (-not $SkipInstall) {
        Write-Output "[1/8] Ensure OpenAIR web dependencies"
        if (-not (Test-Path (Join-Path $RepoRoot "node_modules\.pnpm"))) {
            Push-Location $RepoRoot
            try {
                & $PnpmPath install --frozen-lockfile
                if ($LASTEXITCODE -ne 0) { throw "pnpm install failed with exit code $LASTEXITCODE" }
            }
            finally {
                Pop-Location
            }
        }
        else {
            Write-Output "PASS: existing OpenAIR web dependencies reused"
        }

        Write-Output "[2/8] Install harness dependencies and Chromium"
        Push-Location $HarnessRoot
        try {
            & $NpmPath ci
            if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE" }
            & $NpxPath playwright install chromium
            if ($LASTEXITCODE -ne 0) { throw "playwright chromium install failed with exit code $LASTEXITCODE" }
        }
        finally {
            Pop-Location
        }
    }
    else {
        Write-Output "[1/8] Reuse installed OpenAIR web dependencies"
        Write-Output "[2/8] Reuse installed harness dependencies and Chromium"
    }

    Write-Output "[3/8] Copy minimal isolated API runtime"
    New-Item -ItemType Directory -Force -Path $IsolatedApiRoot | Out-Null
    Copy-DirectoryTree -Source (Join-Path $RepoRoot "services\api\app") -Destination (Join-Path $IsolatedApiRoot "app")
    Copy-FileToDirectory -Source (Join-Path $RepoRoot "services\api\pyproject.toml") -DestinationDirectory $IsolatedApiRoot
    Copy-FileToDirectory -Source (Join-Path $RepoRoot "services\api\uv.lock") -DestinationDirectory $IsolatedApiRoot
    Copy-FileToDirectory -Source (Join-Path $RepoRoot "services\api\requirements.txt") -DestinationDirectory $IsolatedApiRoot
    Copy-DirectoryTree -Source (Join-Path $RepoRoot "fixtures") -Destination (Join-Path $IsolatedRepoRoot "fixtures")
    Copy-DirectoryTree -Source (Join-Path $RepoRoot "schemas") -Destination (Join-Path $IsolatedRepoRoot "schemas")
    Copy-DirectoryTree -Source (Join-Path $RepoRoot "skills") -Destination (Join-Path $IsolatedRepoRoot "skills")
    Write-Output "PASS: ephemeral agent workspace '.claude' is excluded from isolated runtime copy."
    Copy-DirectoryTree -Source (Join-Path $RepoRoot "mocks") -Destination (Join-Path $IsolatedRepoRoot "mocks")
    New-Item -ItemType Directory -Force -Path (Join-Path $IsolatedRepoRoot "data\tasks") | Out-Null

    $RuntimeLabel = if ($HuiyanPlannerLive -and $HuiyanSkillsLive) { "fully live HUIYAN planner and selected-Skill mode" } elseif ($HuiyanPlannerLive) { "bounded HUIYAN planner Live mode" } elseif ($HuiyanSkillsLive) { "HUIYAN selected-Skill Live mode" } elseif ($LiveRun) { "Claude selected-Skill Live mode" } else { "fixture-seeded replay mode" }
    $SeedFixtures = if ($LiveRun -or $HuiyanPlannerLive -or $HuiyanSkillsLive) { "0" } else { "1" }
    $SkillExecMode = if ($LiveRun -or $HuiyanSkillsLive) { "live" } else { "replay" }
    $LiveSkillRuntime = if ($HuiyanSkillsLive) { "huiyan_chat_completions" } else { "" }
    $ResearchStartPlannerMode = if ($HuiyanPlannerLive) { "required" } else { "" }
    $ResearchStartPlannerProvider = if ($HuiyanPlannerLive) { "huiyan_chat_completions" } else { "" }
    Write-Output "[4/8] Start isolated OpenAIR API in $RuntimeLabel"
    Write-Output "OPENAIR_PERSISTENCE_BACKEND=json"
    Write-Output "OPENAIR_AUTH_REQUIRED=1"
    $ApiStdoutPath = Join-Path $ServerLogDir "api.stdout.log"
    $ApiStderrPath = Join-Path $ServerLogDir "api.stderr.log"
    $ApiLauncherScript = Join-Path $ServerLogDir "start-api.ps1"
    $ApiArguments = [string[]]@(
        $ApiLauncher.PrefixArguments +
        @("-u", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", [string]$ApiPort, "--log-level", "debug")
    )
    $ApiArgumentLines = @(
        $ApiArguments | ForEach-Object { "    '" + $_.Replace("'", "''") + "'" }
    ) -join [Environment]::NewLine
    @"
`$ErrorActionPreference = 'Stop'
`$env:OPENAIR_DEV_SEED_FIXTURES = '$SeedFixtures'
`$env:SKILL_EXEC_MODE = '$SkillExecMode'
`$env:OPENAIR_LIVE_SKILL_RUNTIME = '$LiveSkillRuntime'
`$env:OPENAIR_RESEARCH_START_PLANNER_MODE = '$ResearchStartPlannerMode'
`$env:OPENAIR_RESEARCH_START_PLANNER_PROVIDER = '$ResearchStartPlannerProvider'
`$env:OPENAIR_PERSISTENCE_BACKEND = 'json'
`$env:OPENAIR_AUTH_REQUIRED = '1'
`$env:OPENAIR_API_PORT = '$ApiPort'
`$env:OPENAIR_REPO_ROOT = '$($IsolatedRepoRoot.Replace("'", "''"))'
Set-Location '$($IsolatedApiRoot.Replace("'", "''"))'
Write-Output 'HARNESS_API_WRAPPER_STARTED'
`$ApiArgs = @(
$ApiArgumentLines
)
& '$($ApiLauncher.FilePath.Replace("'", "''"))' @ApiArgs
exit `$LASTEXITCODE
"@ | Set-Content -Encoding UTF8 -Path $ApiLauncherScript
    $ApiProcess = Start-LoggedPowerShellFile `
        -ScriptPath $ApiLauncherScript `
        -WorkingDirectory $IsolatedApiRoot `
        -StdoutPath $ApiStdoutPath `
        -StderrPath $ApiStderrPath
    Wait-HttpOkOrProcessExit `
        -Url $ApiHealthUrl `
        -TimeoutSeconds 120 `
        -Label "isolated OpenAIR API" `
        -Process $ApiProcess `
        -StdoutPath $ApiStdoutPath `
        -StderrPath $ApiStderrPath

    Write-Output "[5/8] Start OpenAIR web on isolated port"
    $WebLauncher = Join-Path $ServerLogDir "start-web.ps1"
    @"
`$ErrorActionPreference = 'Stop'
`$env:VITE_OPENAIR_API_BASE_URL = '$ApiBaseUrl'
Set-Location '$($RepoRoot.Replace("'", "''"))'
& '$($PnpmPath.Replace("'", "''"))' --filter '@openair/web' dev --host 127.0.0.1 --port $WebPort --strictPort
exit `$LASTEXITCODE
"@ | Set-Content -Encoding UTF8 -Path $WebLauncher
    $WebStdoutPath = Join-Path $ServerLogDir "web.stdout.log"
    $WebStderrPath = Join-Path $ServerLogDir "web.stderr.log"
    $WebProcess = Start-LoggedPowerShellFile `
        -ScriptPath $WebLauncher `
        -WorkingDirectory $RepoRoot `
        -StdoutPath $WebStdoutPath `
        -StderrPath $WebStderrPath
    Wait-HttpOkOrProcessExit `
        -Url $WebBoardUrl `
        -TimeoutSeconds 45 `
        -Label "OpenAIR web" `
        -Process $WebProcess `
        -StdoutPath $WebStdoutPath `
        -StderrPath $WebStderrPath

    Write-Output "[6/8] Run browser $(if (-not [string]::IsNullOrWhiteSpace($TestSpec)) { "targeted spec: $TestSpec" } else { "baseline" })"
    $env:OPENAIR_PRODUCT_HARNESS_AUTH_REQUIRED = "1"
    $TestStdout = Join-Path $RunDir "playwright.stdout.log"
    $TestStderr = Join-Path $RunDir "playwright.stderr.log"
    $PlaywrightArgs = @("playwright", "test", "--project=chromium-desktop")
    if (-not [string]::IsNullOrWhiteSpace($TestSpec)) {
        # Playwright treats backslashes in a file filter as regular-expression escapes.
        $PlaywrightArgs += $TestSpec.Replace("\", "/")
    }
    $TestProcess = Start-Process -FilePath $NpxPath `
        -ArgumentList $PlaywrightArgs `
        -WorkingDirectory $HarnessRoot `
        -RedirectStandardOutput $TestStdout `
        -RedirectStandardError $TestStderr `
        -Wait `
        -PassThru
    $ExitCode = $TestProcess.ExitCode
    $HarnessExecution = "PASS"
    $NormalizedTestSpec = $TestSpec.Replace("\", "/")
    $ObservedProductOutcome = if ($NormalizedTestSpec.EndsWith("tests/36-c3-source-authenticity-mainline.spec.ts")) {
        "FAIL"
    }
    elseif ($ExitCode -eq 0) {
        "PASS"
    }
    else {
        "FAIL"
    }
    if (Test-Path $TestStdout) { Get-Content $TestStdout }
    if (Test-Path $TestStderr) { Get-Content $TestStderr }

    Write-Output "[7/8] Baseline evidence generated"
    Write-Output "[8/8] Preserve short-path isolated runtime under RUN_DIR\api for review"
}
finally {
    Stop-ProcessSafe -Process $WebProcess
    Stop-ProcessSafe -Process $ApiProcess
    Start-Sleep -Milliseconds 800
    Stop-IsolatedPortListenerSafe -Port $WebPort -Label "OpenAIR web"
    Stop-IsolatedPortListenerSafe -Port $ApiPort -Label "isolated OpenAIR API"

    $Report = @(
        "=== OPENAIR PRODUCT HARNESS P2.1 ISOLATED BASELINE REPORT ===",
        "RUN_DIR=$RunDir",
        "ISOLATED_RUNTIME_DIR=$IsolatedRepoRoot",
        "API_BASE_URL=$ApiBaseUrl",
        "WEB_BASE_URL=$WebBaseUrl",
        "PLAYWRIGHT_EXIT_CODE=$ExitCode",
        "HARNESS_EXECUTION=$HarnessExecution",
        "OBSERVED_PRODUCT_OUTCOME=$ObservedProductOutcome",
        "PRODUCT_SOURCE_MODIFIED_BY_RUNNER=NO_TRACKED_SOURCE_WRITE_ACTIONS",
        "SOURCE_TASK_DATA_MUTATED_BY_RUNNER=NO",
        "EXISTING_SERVERS_REUSED=NO",
        "IMPORTANT: A non-zero Playwright exit code may be an expected product baseline finding. Review html-report, screenshots, gates, network-console and server logs.",
        "IMPORTANT: Send the complete terminal output and RUN_DIR back for review."
    )
    $ReportPath = Join-Path $RunDir "REPORT.txt"
    $Report | Set-Content -Encoding UTF8 -Path $ReportPath
    $Report | ForEach-Object { Write-Output $_ }
}

exit $ExitCode
