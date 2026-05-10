@echo off
chcp 65001 >nul
setlocal

REM =====================================================
REM iPhone 照片整理 - 一键启动
REM 双击即可运行，自动调用 organizer.py
REM =====================================================

REM ---- 路径配置 (按需修改) ----
set "SOURCE=D:\AHAHA\DCIM\Input_Photos"
set "TARGET=D:\AHAHA\DCIM\Organized_Photos"
set "DUPLICATES=D:\AHAHA\DCIM\Duplicates"

REM ---- Python 解释器 (CLAUDE.md 指定的 conda lang 环境) ----
set "PYTHON=D:\soft\Miniconda\envs\lang\python.exe"

REM ---- 切到脚本所在目录，确保相对路径 (exiftool/) 能找到 ----
cd /d "%~dp0"

echo.
echo ============================================================
echo  iPhone 照片整理工具
echo ============================================================
echo  源目录:     %SOURCE%
echo  目标目录:   %TARGET%
echo  重复目录:   %DUPLICATES%
echo  选项:       --phash --workers 8
echo ============================================================
echo.

if not exist "%SOURCE%" (
    echo [错误] 源目录不存在: %SOURCE%
    echo 请先创建并放入待整理的照片。
    pause
    exit /b 2
)

if not exist "%PYTHON%" (
    echo [错误] Python 未找到: %PYTHON%
    echo 请检查 conda 环境路径。
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
if %RC% EQU 0     echo  [成功] 全部归档完成
if %RC% EQU 1     echo  [警告] 有业务失败，请查看上方失败清单
if %RC% EQU 2     echo  [错误] 脚本异常，请查看日志排查
if %RC% EQU 130   echo  [中断] 用户取消，状态已保存，下次运行可续跑
echo ============================================================
echo.
pause
exit /b %RC%
