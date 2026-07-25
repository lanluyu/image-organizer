@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title iPhone Photo Organizer

REM ============================================================
REM  Interactive launcher entry point.
REM
REM  IMPORTANT: keep this file PURE ASCII.
REM  cmd.exe reads a batch file by byte offset and re-reads it
REM  after each command. With multi-byte characters in the file
REM  that offset drifts and cmd resumes parsing in the MIDDLE of
REM  a line, executing fragments as commands. Adding a UTF-8 BOM
REM  or switching the file to GBK does NOT fix it -- both were
REM  tested and still broke. All Chinese text lives in
REM  launcher.ps1, where PowerShell parses it correctly.
REM ============================================================

set "PWSH="
for %%p in (pwsh.exe) do if not defined PWSH if exist "%%~$PATH:p" set "PWSH=%%~$PATH:p"
if not defined PWSH if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%ProgramW6432%\PowerShell\7\pwsh.exe" set "PWSH=%ProgramW6432%\PowerShell\7\pwsh.exe"
if not defined PWSH if exist "%LOCALAPPDATA%\Microsoft\PowerShell\7\pwsh.exe" set "PWSH=%LOCALAPPDATA%\Microsoft\PowerShell\7\pwsh.exe"

if not defined PWSH goto :no_pwsh
if not exist "%~dp0launcher.ps1" goto :no_script

"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
exit /b %ERRORLEVEL%

:no_pwsh
echo.
echo   [ERROR] PowerShell 7 (pwsh.exe) not found.
echo.
echo   This launcher requires PowerShell 7. The Windows built-in
echo   PowerShell 5.1 will not work.
echo.
echo   Install:   winget install --id Microsoft.PowerShell
echo   Download:  https://github.com/PowerShell/PowerShell/releases
echo.
echo   Re-run this file after installing.
echo.
pause
exit /b 2

:no_script
echo.
echo   [ERROR] launcher.ps1 not found. It must sit next to this file:
echo   %~dp0
echo.
pause
exit /b 2
