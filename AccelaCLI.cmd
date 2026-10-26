@echo off
setlocal
set "ACCELACLI_ROOT=%~dp0"

where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py "%ACCELACLI_ROOT%src\main.py" %*
    exit /b %ERRORLEVEL%
)

where python >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python "%ACCELACLI_ROOT%src\main.py" %*
    exit /b %ERRORLEVEL%
)

echo AccelaCLI requires Python 3.13 or newer. 1>&2
exit /b 127
