@echo off
setlocal
set "UNIFIED_UPDATE_DIR=%~dp0"
powershell.exe -NoProfile -Command "$ErrorActionPreference='Stop'; $update=$env:UNIFIED_UPDATE_DIR; $bases=@(); try { $bases=@(Get-ScheduledTask -TaskName 'UnifiedPersonalMCP-AutoConnect' | Where-Object { $_.Description -eq 'Unified Personal MCP automatic connection (managed by install-autostart.ps1).' } | ForEach-Object { $_.Actions.WorkingDirectory } | Where-Object { $_ } | Select-Object -Unique) } catch {}; if ($bases.Count -eq 1) { $base=$bases[0]; Write-Host ('Existing installation: '+$base) } else { $base=Read-Host 'Enter the extracted complete 0.1.10 through 0.1.13 installation folder' }; $base=(Resolve-Path -LiteralPath $base).Path; $python=Join-Path $base 'resources/python/python.exe'; if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Complete installation required; the update contains no Python runtime.' }; & $python -B -I (Join-Path $update 'scripts/prepare_update.py') --base $base --update $update --apply; if ($LASTEXITCODE -ne 0) { throw 'Update did not complete. Inspect the diagnostic and switch result above.' }"
if errorlevel 1 (
  echo Update preparation failed. Read the diagnostic above.
  pause
  exit /b 1
)
pause
