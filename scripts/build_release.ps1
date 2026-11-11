<#
.SYNOPSIS
  Build the Windows release of IEUStudia: the app folder (PyInstaller), a portable zip and the installer (Inno Setup).

.DESCRIPTION
  Output in build\installer\:
    IEUStudia-Setup-<version>.exe     per-user installer (no admin rights), the file to share in GitHub Releases
    IEUStudia-<version>-portable.zip  the same app as a folder (run IEUStudia.exe)
    SHA256SUMS.txt                    the SHA-256 of both, as sha256sum writes it
  Needs uv (https://docs.astral.sh/uv/) and, for the installer, Inno Setup 6 (https://jrsoftware.org/isinfo.php).
#>
param([switch]$NoInstaller)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Push-Location $repo
try {
    $version = (Select-String -Path "src\studia\__init__.py" -Pattern '__version__ = "([^"]+)"').Matches[0].Groups[1].Value
    Write-Host "==> IEUStudia $version" -ForegroundColor Cyan
    uv sync --group build --group dev
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
    uv run pytest -q
    if ($LASTEXITCODE -ne 0) { throw "tests failed" }
    uv run pyinstaller packaging\IEUStudia.spec --noconfirm --distpath build\dist --workpath build\work
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

    Write-Host "==> Self-test of the build" -ForegroundColor Cyan
    $report = Join-Path $repo "build\selftest.json"
    Remove-Item $report -ErrorAction SilentlyContinue
    $p = Start-Process "build\dist\IEUStudia\IEUStudia.exe" -ArgumentList "--selftest", "`"$report`"" -Wait -PassThru
    Get-Content $report
    $ocr = (Get-Content $report -Raw | ConvertFrom-Json).ocr
    Write-Host "Windows' text reading (OCR): $($ocr.status) $($ocr.why) $($ocr.text)"
    if ($p.ExitCode -ne 0) { throw "The self-test of the build failed (see above)." }
    # the portable zip unpacked with Windows' Extract All: every file marked as downloaded from the internet
    & "$PSScriptRoot\selftest_marked.ps1" -Dist "build\dist\IEUStudia"

    New-Item -ItemType Directory -Force "build\installer" | Out-Null
    $zip = "build\installer\IEUStudia-$version-portable.zip"
    Remove-Item $zip -ErrorAction SilentlyContinue
    Compress-Archive -Path "build\dist\IEUStudia" -DestinationPath $zip
    Write-Host "Portable: $zip"

    if (-not $NoInstaller) {
        $iscc = @((Get-Command iscc -ErrorAction SilentlyContinue).Source,
                  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
                  "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $iscc) { throw "Inno Setup 6 was not found (install it, or use -NoInstaller)." }
        & $iscc "/DAppVersion=$version" "packaging\IEUStudia.iss"
        if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
        Write-Host "Installer: build\installer\IEUStudia-Setup-$version.exe" -ForegroundColor Green
    }

    $files = @(Get-ChildItem "build\installer" -File | Where-Object { $_.Name -like "IEUStudia-*$version*" })
    $files | ForEach-Object { "{0}  {1}" -f (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower(), $_.Name } |
        Set-Content "build\installer\SHA256SUMS.txt" -Encoding ascii
    Get-Content "build\installer\SHA256SUMS.txt"
} finally {
    Pop-Location
}
