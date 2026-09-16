# Build the Windows executables.
#
#   powershell -ExecutionPolicy Bypass -File packaging\build_windows.ps1
#
# Produces dist\SINE Templater\ containing both executables and one shared
# _internal folder. Zip that folder to distribute it.
#
# PyInstaller goes into a throwaway virtualenv under .venv-build rather than the
# system Python, so nothing is installed globally and deleting that folder undoes
# everything this script did.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venv = Join-Path $root ".venv-build"
$python = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $python)) {
    Write-Host "Creating build virtualenv in .venv-build ..."
    python -m venv $venv
}

& $python -m pip install --quiet --upgrade pip
& $python -m pip install --quiet pyinstaller

$out = Join-Path $root "dist\SINE Templater"

# A running copy holds dist open, and COLLECT then leaves the previous build in
# place. That looked like a successful build until the icon silently failed to
# change, so stop it happening rather than trusting the log.
Get-Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($out, "OrdinalIgnoreCase") } |
    ForEach-Object { Write-Host "Stopping running $($_.ProcessName) (pid $($_.Id))"; $_.Kill() }

Write-Host "Freezing ..."
& $python -m PyInstaller --noconfirm --clean (Join-Path $root "packaging\sine_templater.spec")

# A native exe's exit code does not trip $ErrorActionPreference; check it.
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

# PyInstaller ships the app and nothing else, but a copy handed to someone else
# has to carry the license, and the README is how they use it. Copied on every
# build rather than left in the folder by hand: COLLECT empties its output
# directory first, so anything dropped in there by hand is gone by the next build.
Write-Host "Copying LICENSE and README.md ..."
Copy-Item (Join-Path $root "LICENSE") -Destination $out -Force
Copy-Item (Join-Path $root "README.md") -Destination $out -Force

# Smoke check. The spec drops modules the tool does not import (see EXCLUDES);
# get that wrong and the failure is an ImportError at *runtime*, not a build
# error, and the test suite runs against the source checkout so it cannot see it.
# So actually start both executables before calling the build good.
$cli = Join-Path $out "sine-templater-cli.exe"

Write-Host "Checking the CLI starts ..."
$null = & $cli build --help
if ($LASTEXITCODE -ne 0) { throw "sine-templater-cli.exe failed to start (exit $LASTEXITCODE)" }

# The window is the real test: it is what pulls in Tk, and Tk is what the trimmed
# Tcl data belongs to. Driven through the console exe so a failure lands on
# stderr rather than in a message box nobody is there to dismiss. Surviving a few
# seconds means Tcl/Tk initialised and the window drew.
Write-Host "Checking the window opens ..."
$log = New-TemporaryFile
$window = Start-Process -FilePath $cli -ArgumentList "gui" -PassThru -NoNewWindow `
    -RedirectStandardError $log.FullName
Start-Sleep -Seconds 6
if ($window.HasExited) {
    $why = (Get-Content $log.FullName -Raw)
    Remove-Item $log.FullName -Force
    throw "the window exited on its own (exit $($window.ExitCode)):`n$why"
}
$window.Kill()
$window.WaitForExit()
Remove-Item $log.FullName -Force -ErrorAction SilentlyContinue
Write-Host "Both executables run."

Write-Host ""
Write-Host "Built:"
Get-ChildItem $out -Filter *.exe | ForEach-Object {
    "  {0}  ({1:N1} MB)" -f $_.Name, ($_.Length / 1MB)
}
"  total: {0:N0} MB" -f ((Get-ChildItem $out -Recurse | Measure-Object Length -Sum).Sum / 1MB)
