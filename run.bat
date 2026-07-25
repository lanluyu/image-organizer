@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  iPhone photo organizer - one-click run with fixed paths.
REM  Edit the three paths below, then double-click this file.
REM
REM  For a guided setup with a dry-run preview, use the other
REM  launcher instead (it opens a PowerShell 7 window).
REM
REM  IMPORTANT: keep this file PURE ASCII.
REM  cmd.exe re-reads a batch file by byte offset after each
REM  command. With multi-byte characters in the file that offset
REM  drifts and cmd resumes parsing in the MIDDLE of a line,
REM  running fragments as commands. A UTF-8 BOM does NOT fix it,
REM  nor does re-encoding to GBK -- both were tested and still
REM  broke. Chinese output below comes from organizer.py itself,
REM  which writes to the console correctly.
REM ============================================================

REM ---- Paths (edit these) ----
set "SOURCE=D:\AHAHA\DCIM\Input_Photos"
set "TARGET=D:\AHAHA\DCIM\Organized_Photos"
set "DUPLICATES=D:\AHAHA\DCIM\Duplicates"

REM ---- Python interpreter (conda env "douyin", see environment.yml) ----
set "PYTHON=D:\soft\Miniconda\envs\douyin\python.exe"

REM ---- Work from the script directory so ./exiftool.exe resolves ----
cd /d "%~dp0"

echo.
echo ============================================================
echo  iPhone Photo Organizer
echo ============================================================
echo  Source:      %SOURCE%
echo  Target:      %TARGET%
echo  Duplicates:  %DUPLICATES%
echo  Options:     --phash --workers 8
echo ============================================================
echo.

if not exist "%SOURCE%" (
    echo  [ERROR] Source folder does not exist:
    echo          %SOURCE%
    echo  Create it and put the photos to organize inside.
    echo.
    pause
    exit /b 2
)

if not exist "%PYTHON%" (
    echo  [ERROR] Python not found:
    echo          %PYTHON%
    echo  Check the conda environment path above.
    echo.
    pause
    exit /b 2
)

"%PYTHON%" -X utf8 "%~dp0organizer.py" ^
    --source     "%SOURCE%" ^
    --target     "%TARGET%" ^
    --duplicates "%DUPLICATES%" ^
    --phash ^
    --workers 8

set "RC=%ERRORLEVEL%"
echo.
echo ============================================================
if %RC% EQU 0   echo  [OK]        All files archived.
if %RC% EQU 1   echo  [WARNING]   Some files failed - see the failure list above.
if %RC% EQU 2   echo  [ERROR]     Script error - see the failure list above.
if %RC% EQU 130 echo  [CANCELLED] Interrupted by user. Progress saved, re-run to resume.
echo ============================================================
echo.
pause
exit /b %RC%
