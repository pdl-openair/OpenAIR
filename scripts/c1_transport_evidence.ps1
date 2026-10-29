# Disposable PostgreSQL + controlled loopback Provider proxy verification for C1.
$ErrorActionPreference = "Stop"

function Get-FreeLoopbackPort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
    $listener.Stop()
    return [int]$port
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$apiRoot = Join-Path $repoRoot "services\api"
$python = Join-Path $apiRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "C1 isolated Python environment is unavailable"
}

$suffix = ([guid]::NewGuid().ToString("N")).Substring(0, 10)
$container = "openair-c1-test-transport-pg-$suffix"
$database = "openair_c1_test_transport_$suffix"
$user = "c1_test_transport_$suffix"
$password = [guid]::NewGuid().ToString("N")
$port = Get-FreeLoopbackPort

try {
    docker run -d --name $container `
        -e "POSTGRES_USER=$user" `
        -e "POSTGRES_PASSWORD=$password" `
        -e "POSTGRES_DB=$database" `
        -p "127.0.0.1:${port}:5432" `
        postgres:17-alpine | Out-Null

    $ready = $false
    for ($i = 0; $i -lt 60; $i++) {
        docker exec $container pg_isready -U $user -d $database *> $null
        if ($LASTEXITCODE -eq 0) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        throw "C1 disposable PostgreSQL did not become ready"
    }

    $env:C1_TRANSPORT_TEST_DATABASE_URL = "postgresql://${user}:${password}@127.0.0.1:${port}/${database}"
    $env:OPENAIR_ENV = "test"
    $env:OPENAIR_DEV_SEED_FIXTURES = "0"
    $env:OPENAIR_PERSISTENCE_BACKEND = "postgres"
    $env:NO_PROXY = "127.0.0.1,localhost"
    $env:no_proxy = "127.0.0.1,localhost"

    $testPath = Join-Path $apiRoot "tests\integration\test_c1_provider_transport_evidence.py"
    & $python -m pytest -q -s $testPath
    if ($LASTEXITCODE -ne 0) {
        throw "C1 transport evidence verification failed"
    }
}
finally {
    Remove-Item Env:C1_TRANSPORT_TEST_DATABASE_URL -ErrorAction SilentlyContinue
    docker rm -f $container *> $null
}
