param(
    [string]$Python = "python",
    [string]$BuildDirectory = "",
    [string]$RuntimeHome = ""
)
$ErrorActionPreference = "Stop"
& $Python -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "Managed Windows installer requires Python 3.11 or newer" }
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
if (-not $BuildDirectory) { $BuildDirectory = Join-Path $repoRoot "build\managed" }
if (-not $RuntimeHome) { $RuntimeHome = Join-Path $env:LOCALAPPDATA "JevDecision" }
$buildRoot = [IO.Path]::GetFullPath($BuildDirectory)
$managedHome = [IO.Path]::GetFullPath($RuntimeHome)
New-Item -ItemType Directory -Path $managedHome -Force | Out-Null
$managedHome = (& $Python -c "from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())" $managedHome).Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve physical runtime home" }
$wheelDirectory = Join-Path $buildRoot ([guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $wheelDirectory -Force | Out-Null
& $Python -m pip wheel --no-deps --wheel-dir $wheelDirectory $repoRoot
if ($LASTEXITCODE -ne 0) { throw "Wheel build failed" }
$wheel = @(Get-ChildItem -LiteralPath $wheelDirectory -Filter "jev_decision-0.3.0-*.whl")
if ($wheel.Count -ne 1) { throw "Expected exactly one version 0.3.0 wheel" }
$wheelHash = (Get-FileHash -LiteralPath $wheel[0].FullName -Algorithm SHA256).Hash.ToLowerInvariant()
$runtimePath = Join-Path $managedHome ("runtimes\0.3.0-" + $wheelHash.Substring(0, 12))
$jevPython = Join-Path $runtimePath "Scripts\python.exe"
$packageManifest = Join-Path $runtimePath "installation.json"
$installPackage = $false
if (Test-Path -LiteralPath $runtimePath) {
    if (-not (Test-Path -LiteralPath $packageManifest)) { throw "Existing runtime is incomplete; use a new build directory after inspection" }
    $existing = Get-Content -LiteralPath $packageManifest -Raw | ConvertFrom-Json
    if ($existing.wheel_sha256 -ne $wheelHash) { throw "Existing runtime does not match this artifact" }
} else {
    & $Python -m venv $runtimePath
    if ($LASTEXITCODE -ne 0) { throw "Runtime creation failed" }
    $installPackage = $true
}
# Resolve an existing executable, not just its directory: MSIX can expose a
# merged directory view while redirecting the actual files into LocalCache.
$jevPython = (& $jevPython -I -c "from pathlib import Path; import sys; print(Path(sys.executable).resolve())").Trim()
if ($LASTEXITCODE -ne 0) { throw "Unable to resolve physical runtime interpreter" }
$runtimePath = Split-Path -Parent (Split-Path -Parent $jevPython)
$managedHome = Split-Path -Parent (Split-Path -Parent $runtimePath)
$packageManifest = Join-Path $runtimePath "installation.json"
if ($installPackage) {
    & $jevPython -m pip install ($wheel[0].FullName + "[mcp,setup]")
    if ($LASTEXITCODE -ne 0) { throw "Package installation failed" }
    & $jevPython -I -c "import jev_decision; assert jev_decision.__version__ == '0.3.0'"
    if ($LASTEXITCODE -ne 0) { throw "Installed version verification failed" }
}
& $jevPython -I -c "from jev_decision.mcp import create_sdk_server; create_sdk_server()"
if ($LASTEXITCODE -ne 0) { throw "MCP dependency verification failed; rebuild in a new environment" }
[IO.File]::WriteAllText((Join-Path $runtimePath "jev-runtime-home.txt"), $managedHome, (New-Object Text.UTF8Encoding($false)))
$record = [ordered]@{
        version = "0.3.0"
        wheel_sha256 = $wheelHash
        python = $jevPython
        pythonw = (Join-Path $runtimePath "Scripts\pythonw.exe")
        cli = (Join-Path $runtimePath "Scripts\jev.exe")
        mcp = (Join-Path $runtimePath "Scripts\jev-mcp.exe")
        installed_utc = [DateTime]::UtcNow.ToString("o")
}
[IO.File]::WriteAllText($packageManifest, ($record | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
$manifest = Get-Content -LiteralPath $packageManifest -Raw | ConvertFrom-Json
# Keep previous versions for reversible launcher updates. No credential is read.
$manifest | ConvertTo-Json
