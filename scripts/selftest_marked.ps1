<#
.SYNOPSIS
  The release build's self-test once more, on a copy whose files carry Windows' "downloaded from the internet" mark,
  as Windows' Extract All leaves the portable zip. .NET refuses marked assemblies, so IEUStudia must unblock its own
  (src/studia/app.py, _unblock_bundle) before it loads its window's parts; this fails the build if it doesn't.

.EXAMPLE
  .\scripts\selftest_marked.ps1 -Dist build\dist\IEUStudia
#>
param([string]$Dist = "build\dist\IEUStudia")
$ErrorActionPreference = "Stop"
$dist = (Resolve-Path $Dist).Path
$work = Join-Path (Split-Path -Parent $dist) "IEUStudia-marked"
if (Test-Path $work) { Remove-Item -Recurse -Force $work }
Copy-Item -Recurse $dist $work
try {
    # Extract All marks every file; the program files are the ones anything checks (marking all takes minutes)
    $mark = "[ZoneTransfer]`r`nZoneId=3`r`nHostUrl=https://github.com/"
    Get-ChildItem $work -Recurse -File -Include *.dll, *.pyd, *.exe |
        ForEach-Object { Set-Content -LiteralPath $_.FullName -Stream Zone.Identifier -Value $mark }
    $report = Join-Path (Split-Path -Parent $dist) "selftest-marked.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    # started directly (not through the shell, which would ask about a marked program first)
    $start = New-Object System.Diagnostics.ProcessStartInfo (Join-Path $work "IEUStudia.exe"), "--selftest `"$report`""
    $start.UseShellExecute = $false
    $p = [System.Diagnostics.Process]::Start($start)
    $p.WaitForExit()
    if (Test-Path $report) { Get-Content $report }
    if ($p.ExitCode -ne 0) { throw "The self-test of a copy marked as downloaded from the internet failed (see above)." }
    $still = @(Get-ChildItem (Join-Path $work "_internal\pythonnet\runtime") -Filter *.dll | Where-Object {
        Get-Item -LiteralPath $_.FullName -Stream Zone.Identifier -ErrorAction SilentlyContinue })
    if ($still.Count) { throw "IEUStudia didn't unblock its .NET files: $($still.Name -join ', ')" }
    Write-Host "The marked copy unblocked itself and passed its self-test." -ForegroundColor Green
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
