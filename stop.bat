@echo off
rem NOTE: keep this file GBK/ANSI(cp936) + CRLF. Do NOT save as UTF-8.
setlocal
cd /d "%~dp0"

echo ============================================================
echo   RAG NoteBook Docker 停止
echo ============================================================
echo.

docker info >nul 2>nul
if errorlevel 1 (
    echo [错误] Docker 引擎未运行，无需停止。
    goto :fail
)

echo 正在停止全部容器（数据将保留，下次运行 start.bat 即可恢复）...
docker compose down
if errorlevel 1 (
    echo [错误] 停止失败，请查看上方日志排查。
    goto :fail
)

echo.
echo 已停止全部容器，数据已保留。
goto :end

:fail
echo.
pause
exit /b 1

:end
echo 按任意键关闭窗口...
pause >nul
exit /b 0
