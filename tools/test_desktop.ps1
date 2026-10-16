param([string]$Python = 'python', [string]$PortablePython = '', [switch]$CleanInstall, [int]$Screen = -1)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$qa = Join-Path $project ('local-only\desktop-test-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $qa -Force | Out-Null
$oldPythonPath = $env:PYTHONPATH
$oldDataHome = $env:ASSET_LAB_HOME
$oldScreen = $env:ASSET_LAB_TEST_SCREEN
$env:ASSET_LAB_TEST_SCREEN = [string]$Screen
$env:ASSET_LAB_HOME = $qa
$env:PYTHONPATH = (Join-Path $project 'src') + ';' + (Join-Path $project 'tests')
try {
    & $Python -c 'from fixtures import make_game; from pathlib import Path; import sys; make_game(Path(sys.argv[1]))' $qa
    if ($LASTEXITCODE) { throw 'Could not make desktop fixture' }
    $app = Join-Path $project 'dist\AssetLab.exe'
    if ($PortablePython -or $CleanInstall) {
        # A distribution test: EXE outside the checkout, with a copy of an installed portable runtime.
        Copy-Item -LiteralPath $app -Destination $qa
        $app = Join-Path $qa 'AssetLab.exe'
        New-Item -ItemType Directory -Path (Join-Path $qa 'home') -Force | Out-Null
        if (-not $CleanInstall) { Copy-Item -LiteralPath $PortablePython -Destination (Join-Path $qa 'home\python') -Recurse }
    }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start(); $port = $listener.LocalEndpoint.Port; $listener.Stop()
    $report = Join-Path $qa 'result.json'
    $arguments = '--offline --port ' + $port + ' --home "' + $qa + '\home" --workspace "' + $qa + '\workspace" --game "' + $qa + '\Mitos.is The Game" --desktop-smoke "' + $report + '"'
    $process = Start-Process -FilePath $app -ArgumentList $arguments -WindowStyle Hidden -PassThru
    try {
        $limit = if ($CleanInstall) { 600 } else { 120 }
        $started = Get-Date
        while (-not $process.WaitForExit(1000)) {
            if (((Get-Date) - $started).TotalSeconds -gt $limit) { throw 'Desktop smoke test timed out' }
        }
        if ($process.ExitCode -ne 0) { throw ('Desktop failed: ' + (Get-Content $report -Raw -ErrorAction SilentlyContinue)) }
        $result = Get-Content $report -Raw | ConvertFrom-Json
        $shutdown = Get-Content ($report + '.shutdown.json') -Raw | ConvertFrom-Json
        # The backend writes its timing just before Form.Close/WinForms disposal. Include that final cleanup too.
        $exitMs = [Math]::Round($shutdown.backendStoppedMs + ($process.ExitTime.ToUniversalTime() - (Get-Item -LiteralPath ($report + '.shutdown.json')).LastWriteTimeUtc).TotalMilliseconds)
        @{ processExitMs = $exitMs; monitor = $shutdown.monitor } | ConvertTo-Json | Set-Content -LiteralPath ($report + '.process-exit.json')
        if (-not ($result.ready -and $result.editor -and $result.desktop -and $shutdown.stopped)) { throw 'Desktop or shutdown assertion failed' }
        if (-not (Test-Path ($report + '.png'))) { throw 'Missing desktop screenshot' }
        if ($shutdown.hideAfterConfirmationMs -gt 250) { throw 'Close did not hide promptly after confirmation' }
        $shutdown | ConvertTo-Json -Compress
        "Process exit after close request: approximately $exitMs ms"
        "PASS: desktop loads, editor is ready, backend stops. Evidence: $report"
    } finally {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
    }
} finally { $env:PYTHONPATH = $oldPythonPath; $env:ASSET_LAB_HOME = $oldDataHome; $env:ASSET_LAB_TEST_SCREEN = $oldScreen }
