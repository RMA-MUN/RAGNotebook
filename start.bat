@echo off
rem NOTE: keep this file GBK/ANSI(cp936) + CRLF. Do NOT save as UTF-8.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo   RAG NoteBook Docker 一键启动
echo ============================================================
echo.

where docker >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Docker，请先安装 Docker Desktop：https://www.docker.com/products/docker-desktop/
    goto :fail
)

docker info >nul 2>nul
if errorlevel 1 (
    echo [错误] Docker 引擎未运行，请先启动 Docker Desktop 并等待其就绪。
    goto :fail
)

if not exist "backend\.env" (
    copy "backend\.env.example" "backend\.env" >nul
    echo [提示] 已生成 backend\.env（从 .env.example 复制）。
    echo        首次使用请编辑该文件，填写 OPENAI_API_KEY 等模型配置；
    echo        填好后重新运行本脚本即可。
    echo.
)

echo [1/3] 构建并启动全部容器（首次构建约 10 分钟，请耐心等待）...
docker compose up -d --build
if errorlevel 1 (
    echo [错误] 容器启动失败，请查看上方日志排查。
    goto :fail
)

echo [2/3] 等待后端服务就绪...
set /a tries=0
:waitloop
docker compose exec -T backend curl -fsS http://127.0.0.1:8000/health >nul 2>nul
if errorlevel 1 (
    set /a tries+=1
    if !tries! GEQ 30 (
        echo [警告] 后端长时间未就绪，请执行 docker compose logs backend 查看日志。
        goto :fail
    )
    timeout /t 3 /nobreak >nul
    goto :waitloop
)

echo [3/3] 启动成功，正在打开浏览器...
echo.
echo   前端页面:    http://localhost:3000    （默认账号 admin / admin1234）
echo   后端 API:    http://localhost:8000/docs
echo.
echo   修改 backend\.env 中的模型 Key 后，执行 docker compose restart backend 即可生效。
echo   数据已持久化在 Docker 数据卷与 backend\ 目录，重启/重建容器不会丢失。
echo.
start "" http://localhost:3000
goto :end

:fail
echo.
echo 启动失败，请参考 docs\troubleshooting.md 的 Docker 部署小节排查。
pause
exit /b 1

:end
echo 按任意键关闭窗口...
pause >nul
exit /b 0
