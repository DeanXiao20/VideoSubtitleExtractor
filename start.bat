@echo off
chcp 65001 >nul 2>&1
title VideoSubtitleExtractor

echo ============================================
echo   视频双语字幕提取器
echo ============================================
echo.

:: Kill existing processes on ports 8002 and 8654
for %%p in (8002 8654) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%p .*LISTENING" 2^>nul') do (
        echo 关闭旧进程 PID=%%a ^(端口 %%p^)...
        taskkill /PID %%a /F >nul 2>&1
    )
)
timeout /t 2 /nobreak >nul

:: Clear Python cache
if exist "__pycache__" (
    echo 清理缓存...
    rd /s /q "__pycache__" 2>nul
    rd /s /q "src\__pycache__" 2>nul
)

:: Check .env
if not exist ".env" (
    if exist ".env.example" (
        echo [提示] 未找到 .env 文件，已从 .env.example 复制
        copy ".env.example" ".env" >nul
        echo         请编辑 .env 配置 LLM_API_KEY 后重启
    ) else (
        echo [提示] 建议创建 .env 文件配置 LLM API Key
    )
    echo.
)

:: Check Python
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [错误] 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

:: Check dependencies
python -c "import streamlit" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [提示] 正在安装依赖...
    pip install -r requirements.txt
    echo.
)

:: Start
echo 正在启动... 访问 http://localhost:8002
echo 按 Ctrl+C 停止
echo.
python run.py
