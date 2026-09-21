param([string]$Python = ".venv-host\Scripts\python.exe")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& $Python -m pip install pywebview==6.0 pythonnet==3.1.0 pyinstaller==6.22.0
if ($LASTEXITCODE -ne 0) { throw "Fixture dependencies failed" }
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name UPM-WebView2-Fixture --distpath verification/fixture-bin --workpath verification/fixture-build --specpath verification --collect-all webview --collect-all pythonnet --hidden-import psutil fixtures/webview2/app.py
if ($LASTEXITCODE -ne 0) { throw "Fixture build failed" }

