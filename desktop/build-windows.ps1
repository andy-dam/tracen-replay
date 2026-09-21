# Builds the Windows bundle of the local application: one folder with the
# service, the client inside it, an embeddable Python with the analyzer and
# its pinned wheels (the DirectML build of ONNX Runtime, so any DirectX 12
# card is used), the OCR models, the learned card reader, ffmpeg, and a
# launcher. Zipped, it is near a gigabyte; unzipped anywhere, "Tracen
# Replay.cmd" starts the service with its data under the user's AppData
# and opens the browser.
#
#   pwsh desktop/build-windows.ps1 [-Version v0.2.0] [-Out dist/windows]
#
# Needs: Go, Node, curl, and a Python on PATH to drive pip inside the
# embeddable interpreter. Run from the repository root.
param(
    [string]$Version = "dev",
    [string]$Out = "dist/windows",
    [string]$PythonVersion = "3.13.7",
    [string]$FfmpegZip = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$bundle = Join-Path $Out "tracen-replay"
if (Test-Path $bundle) { Remove-Item -Recurse -Force $bundle }
New-Item -ItemType Directory -Force $bundle | Out-Null
$cache = Join-Path $Out "cache"
New-Item -ItemType Directory -Force $cache | Out-Null

Write-Host "== client and service ($Version)"
Push-Location web
npm ci
npm run build
Pop-Location
$env:CGO_ENABLED = "0"
go build -trimpath -ldflags "-s -w -X main.version=$Version" -o (Join-Path $bundle "tracen.exe") ./cmd/tracen

Write-Host "== embeddable Python $PythonVersion"
$pyZip = Join-Path $cache "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path $pyZip)) { curl.exe -sSL -o $pyZip "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" }
$py = Join-Path $bundle "python"
Expand-Archive -Path $pyZip -DestinationPath $py -Force
# The embeddable interpreter ignores site-packages unless its ._pth says
# otherwise; pip is installed into it with get-pip.
$pth = Get-ChildItem $py -Filter "python*._pth" | Select-Object -First 1
$lines = Get-Content $pth.FullName | ForEach-Object { if ($_ -eq "#import site") { "import site" } else { $_ } }
$lines += "Lib\site-packages"
Set-Content -Path $pth.FullName -Value $lines
$getPip = Join-Path $cache "get-pip.py"
if (-not (Test-Path $getPip)) { curl.exe -sSL -o $getPip "https://bootstrap.pypa.io/get-pip.py" }
& (Join-Path $py "python.exe") $getPip --no-warn-script-location
Write-Host "== analyzer and its wheels"
Copy-Item -Recurse analyzer (Join-Path $bundle "analyzer")
Remove-Item -Recurse -Force (Join-Path $bundle "analyzer/lab"), (Join-Path $bundle "analyzer/tests") -ErrorAction SilentlyContinue
# The embeddable interpreter has no setuptools and cannot make the isolated
# build environment pip would otherwise use, so setuptools goes in first
# and the analyzer is built in place.
& (Join-Path $py "python.exe") -m pip install --no-warn-script-location setuptools wheel
& (Join-Path $py "python.exe") -m pip install --no-warn-script-location --no-build-isolation -c docker/constraints-windows.txt "$(Join-Path $bundle 'analyzer')[vision]"
if ($LASTEXITCODE -ne 0) { throw "pip install of the analyzer failed" }

Write-Host "== OCR models"
$models = Join-Path $bundle "models"
New-Item -ItemType Directory -Force $models | Out-Null
& (Join-Path $py "python.exe") -c "from tracen_replay.vision import NeuralReader; print(sorted(NeuralReader(r'$models').models))"
Push-Location $models
Get-Content (Join-Path $root "docker/models.sha256") | ForEach-Object {
    $hash, $name = $_ -split "\s+", 2
    $actual = (Get-FileHash -Algorithm SHA256 $name).Hash.ToLower()
    if ($actual -ne $hash) { throw "model $name hashes to $actual, expected $hash" }
}
Pop-Location

Write-Host "== ffmpeg"
$ffZip = Join-Path $cache "ffmpeg.zip"
if (-not (Test-Path $ffZip)) { curl.exe -sSL -o $ffZip $FfmpegZip }
$ffTmp = Join-Path $cache "ffmpeg"
if (Test-Path $ffTmp) { Remove-Item -Recurse -Force $ffTmp }
Expand-Archive -Path $ffZip -DestinationPath $ffTmp -Force
$ffBin = Get-ChildItem $ffTmp -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
New-Item -ItemType Directory -Force (Join-Path $bundle "ffmpeg") | Out-Null
Copy-Item (Join-Path $ffBin.DirectoryName "ffmpeg.exe"), (Join-Path $ffBin.DirectoryName "ffprobe.exe") (Join-Path $bundle "ffmpeg")
Get-ChildItem $ffTmp -Recurse -Filter "LICENSE*" | Select-Object -First 1 | Copy-Item -Destination (Join-Path $bundle "ffmpeg/LICENSE.txt")

Write-Host "== launcher and notices"
Copy-Item (Join-Path $PSScriptRoot "Tracen Replay.cmd") $bundle
Copy-Item LICENSE.md, README.md $bundle
Set-Content -Path (Join-Path $bundle "VERSION") -Value $Version
Copy-Item (Join-Path $PSScriptRoot "README-bundle.md") (Join-Path $bundle "README-first.md")

Write-Host "== zip"
$zip = Join-Path $Out "tracen-replay-windows-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path $bundle -DestinationPath $zip
Write-Host "built $zip ($([math]::Round((Get-Item $zip).Length / 1MB)) MB)"
