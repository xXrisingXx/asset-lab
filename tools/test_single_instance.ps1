param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$created = $false
$probe = [Threading.Mutex]::new($false, 'Local\AssetLab.Desktop', [ref]$created)
$probe.Dispose()
if (-not $created) { throw 'Close the existing desktop Asset Lab before running the isolated singleton test.' }
$project = Split-Path -Parent $PSScriptRoot
$qa = Join-Path $project ('local-only\singleton-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path (Join-Path $qa 'home') -Force | Out-Null
$oldHome = $env:ASSET_LAB_HOME; $oldPath = $env:PYTHONPATH
$env:ASSET_LAB_HOME = $qa; $env:PYTHONPATH = (Join-Path $project 'src') + ';' + (Join-Path $project 'tests')
$first = $null
try {
    & $Python -c 'from fixtures import make_game; from pathlib import Path; import sys; make_game(Path(sys.argv[1]))' $qa
    if ($LASTEXITCODE) { throw 'Fixture failed' }
    Set-Content -LiteralPath (Join-Path $qa 'settings.json') -Value '{"language":"en","check_updates":false}'
    Set-Content -LiteralPath (Join-Path $qa 'home\ai-declined') -Value 'test'
    $a = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0); $a.Start()
    $b = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0); $b.Start()
    $port = $a.LocalEndpoint.Port; $other = $b.LocalEndpoint.Port; $a.Stop(); $b.Stop()
    $app = Join-Path $project 'dist\AssetLab.exe'; $copy = Join-Path $qa 'AssetLab.exe'
    Copy-Item -LiteralPath $app -Destination $copy
    $args1 = '--offline --no-browser --port ' + $port + ' --home "' + $qa + '\home" --workspace "' + $qa + '\workspace" --game "' + $qa + '\Mitos.is The Game"'
    $first = Start-Process $app -ArgumentList $args1 -WindowStyle Hidden -PassThru
    $health = $null
    for ($i=0; $i -lt 100; $i++) {
        try { $health = Invoke-RestMethod "http://127.0.0.1:$port/api/health" -TimeoutSec 1; break } catch { Start-Sleep -Milliseconds 200 }
    }
    if (-not $health) { throw 'First instance did not start' }
    $second = Start-Process $copy -ArgumentList ('--offline --no-browser --port ' + $other) -WindowStyle Hidden -PassThru
    if (-not $second.WaitForExit(10000) -or $second.ExitCode -ne 0) { throw 'Duplicate did not exit cleanly' }
    $after = Invoke-RestMethod "http://127.0.0.1:$port/api/health"
    if ($after.pid -ne $health.pid) { throw 'Duplicate replaced the backend' }
    $extra = $null
    try { $extra = Invoke-RestMethod "http://127.0.0.1:$other/api/health" -TimeoutSec 1 } catch { }
    if ($extra) { throw 'Duplicate launched another backend' }
    $status = Invoke-RestMethod "http://127.0.0.1:$port/api/status"
    Invoke-RestMethod "http://127.0.0.1:$port/api/shutdown" -Method Post -Headers @{'X-Lab-Token'=$status.token} | Out-Null
    if (-not $first.WaitForExit(30000)) { throw 'First instance did not stop' }
    'PASS: second EXE from another folder, using a different port, exits and preserves the first instance.'
} finally {
    if ($first -and -not $first.HasExited) { Stop-Process -Id $first.Id -Force }
    $env:ASSET_LAB_HOME = $oldHome; $env:PYTHONPATH = $oldPath
}
