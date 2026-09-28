@echo off
setlocal
chcp 65001 >nul
set "PYTHONHOME="
set "PYTHONPATH="
set "TCL_LIBRARY="
set "TK_LIBRARY="
set "UNIFIED_DIAG_DIR=%LOCALAPPDATA%\UnifiedPersonalMCP\diagnostics"
if not defined LOCALAPPDATA set "UNIFIED_DIAG_DIR=%TEMP%\UnifiedPersonalMCP-diagnostics"
if not exist "%UNIFIED_DIAG_DIR%" mkdir "%UNIFIED_DIAG_DIR%"
echo Unified Personal MCP startup diagnostics
echo Windows information:
ver
echo Architecture: %PROCESSOR_ARCHITECTURE%
echo This check does not read tunnel keys or start a connection.
set "UNIFIED_DIAG_EXIT=1"
if not exist "%~dp0resources\python\python.exe" goto missing
> "%UNIFIED_DIAG_DIR%\startup-console.txt" echo Unified Personal MCP startup diagnostics
ver >> "%UNIFIED_DIAG_DIR%\startup-console.txt"
echo Architecture: %PROCESSOR_ARCHITECTURE% >> "%UNIFIED_DIAG_DIR%\startup-console.txt"
"%~dp0resources\python\python.exe" -I -B -X utf8 "%~dp0app\startup_diagnostics.py" --assets "%~dp0resources" --output "%UNIFIED_DIAG_DIR%\startup-latest.json" >> "%UNIFIED_DIAG_DIR%\startup-console.txt" 2>&1
set "UNIFIED_DIAG_EXIT=%ERRORLEVEL%"
echo Exit code: %UNIFIED_DIAG_EXIT% >> "%UNIFIED_DIAG_DIR%\startup-console.txt"
type "%UNIFIED_DIAG_DIR%\startup-console.txt"
echo.
echo Exit code: %UNIFIED_DIAG_EXIT%
echo Logs: %UNIFIED_DIAG_DIR%
echo If Python could not start, send the exit code and Windows system type.
goto finished
:missing
echo Missing resources\python\python.exe. Extract the complete ZIP before running.
:finished
echo.
pause
exit /b %UNIFIED_DIAG_EXIT%
