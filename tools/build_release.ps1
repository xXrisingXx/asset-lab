# Build Asset Lab: the lab code package and the single launcher exe that carries it.
#   dist\asset-lab-app.zip   the code an update installs (src, bootstrap.json, VERSION, licences)
#   dist\AssetLab.exe        launcher + bootstrapper with that package inside
#   launcher\Asset Lab.exe   the same exe for this source checkout (it runs ..\src directly)
# Research data and tools stay out of the package. CI passes -Version 1.0.<run number>.
param([string]$Version, [string]$Repository)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Version) { $Version = (Get-Content (Join-Path $root 'VERSION') -Raw).Trim() }
$dist = Join-Path $root 'dist'
$stage = Join-Path $dist 'stage'
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null

# What players get: the app, its web UI, and the manifest of what to download. Not the EXE research data.
$skip = @('exe-references.json', 'decoded-visual-references.json', 'visual-findings.json')
Get-ChildItem (Join-Path $root 'src') -Recurse -File | Where-Object {
    $_.FullName -notmatch '\\__pycache__\\' -and $skip -notcontains $_.Name -and $_.Extension -ne '.pyc'
} | ForEach-Object {
    $rel = $_.FullName.Substring($root.Length + 1)
    $dest = Join-Path $stage $rel
    New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
    Copy-Item $_.FullName $dest
}
foreach ($f in 'bootstrap.json', 'LICENSE', 'README.md', 'THIRD-PARTY-NOTICES.md') { if (Test-Path (Join-Path $root $f)) { Copy-Item (Join-Path $root $f) $stage } }
Set-Content -Path (Join-Path $stage 'VERSION') -Value $Version -NoNewline -Encoding ASCII
# Updates come from this repository's releases (CI passes owner/name).
if ($Repository) {
    $manifest = Join-Path $stage 'bootstrap.json'
    $text = [IO.File]::ReadAllText($manifest) -replace '"repo":\s*"[^"]*"', ('"repo": "' + $Repository + '"')
    [IO.File]::WriteAllText($manifest, $text, (New-Object Text.UTF8Encoding $false))
}

$zip = Join-Path $dist 'asset-lab-app.zip'
if (Test-Path $zip) { Remove-Item $zip }
Add-Type -AssemblyName System.IO.Compression.FileSystem
[IO.Compression.ZipFile]::CreateFromDirectory($stage, $zip, [IO.Compression.CompressionLevel]::Optimal, $false)

# Resources the exe carries: the package, the download manifest, its version and the logo for the loader.
$res = Join-Path $dist 'res'
New-Item -ItemType Directory -Force $res | Out-Null
Copy-Item $zip (Join-Path $res 'app.zip') -Force
Copy-Item (Join-Path $stage 'bootstrap.json') (Join-Path $res 'bootstrap.json') -Force
Set-Content -Path (Join-Path $res 'VERSION') -Value $Version -NoNewline -Encoding ASCII
Copy-Item (Join-Path $root 'src\web\logo.png') (Join-Path $res 'logo.png') -Force

$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319'
$csc = Join-Path $framework 'csc.exe'
$exe = Join-Path $dist 'AssetLab.exe'
$parts = $Version.Split('.') + @('0', '0', '0', '0')
$assemblyVersion = ($parts[0..3] | ForEach-Object { [int]($_ -replace '\D', '') }) -join '.'
$source = [IO.File]::ReadAllText((Join-Path $root 'launcher\AssetLab.cs'), [Text.Encoding]::UTF8)
$source = $source -replace 'AssemblyVersion\("[^"]*"\)', "AssemblyVersion(""$assemblyVersion"")"
$built = Join-Path $res 'AssetLab.cs'
[IO.File]::WriteAllText($built, $source, (New-Object Text.UTF8Encoding $true))
& $csc /nologo /codepage:65001 /target:winexe /optimize+ /platform:anycpu "/win32icon:$(Join-Path $root 'launcher\application.ico')" `
    /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.Core.dll `
    "/reference:$framework\System.IO.Compression.dll" "/reference:$framework\System.IO.Compression.FileSystem.dll" `
    "/resource:$(Join-Path $res 'app.zip'),app.zip" "/resource:$(Join-Path $res 'bootstrap.json'),bootstrap.json" `
    "/resource:$(Join-Path $res 'VERSION'),VERSION" "/resource:$(Join-Path $res 'logo.png'),logo.png" `
    "/out:$exe" $built
if ($LASTEXITCODE -ne 0) { throw 'The launcher did not compile; see the errors above.' }
Remove-Item -Recurse -Force $stage, $res

# The source checkout's own copy (it detects ..\src and runs that code directly).
$local = Join-Path $root 'launcher\Asset Lab.exe'
try { Copy-Item $exe $local -Force } catch { Write-Warning "launcher\Asset Lab.exe is in use; close Asset Lab and build again to refresh it." }
Get-Item $exe, $zip | ForEach-Object { '{0}  {1:N0} KB  sha256 {2}' -f $_.Name, ($_.Length / 1KB), (Get-FileHash $_.FullName).Hash.ToLower() }
"Asset Lab $Version built."
