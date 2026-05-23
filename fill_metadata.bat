@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_PATH=%SCRIPT_DIR%fill_ttml_metadata.py"

if not exist "%SCRIPT_PATH%" (
    echo [错误] 找不到脚本: "%SCRIPT_PATH%"
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python 命令。请先安装 Python 3.10 或更新版本。
    pause
    exit /b 1
)

echo TTML 元数据快速填充
echo.
set /p "TARGET_DIR=请输入要处理的目录路径: "

if not defined TARGET_DIR (
    echo [错误] 目录不能为空。
    pause
    exit /b 1
)

for %%I in ("%TARGET_DIR%") do set "TARGET_DIR=%%~I"

if not defined TARGET_DIR (
    echo [错误] 目录不能为空。
    pause
    exit /b 1
)

if not exist "%TARGET_DIR%\." (
    echo [错误] 目录不存在: "%TARGET_DIR%"
    pause
    exit /b 1
)

echo.
echo [预览] 将先执行 dry-run，不会修改文件。
python "%SCRIPT_DIR%fill_ttml_metadata.py" "%TARGET_DIR%" --dry-run --non-interactive
if errorlevel 1 (
    echo.
    echo [错误] dry-run 失败，已停止。请根据上面的错误信息处理后重试。
    pause
    exit /b 1
)

echo.
set /p "CONFIRM=确认要真实写入吗？输入 Y 后继续: "

if /i "%CONFIRM%"=="Y" (
    echo.
    echo [写入] 正在更新 TTML，原文件会由 Python 脚本生成 .bak 备份。
    python "%SCRIPT_DIR%fill_ttml_metadata.py" "%TARGET_DIR%" --non-interactive
    if errorlevel 1 (
        echo.
        echo [错误] 写入失败。请根据上面的错误信息处理后重试。
        pause
        exit /b 1
    )
    echo.
    echo 处理完成。
) else (
    echo.
    echo 已取消，未修改文件。
)

pause
