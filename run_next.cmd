@echo off
setlocal

cd /d "%~dp0"

set "HF_HOME=%~dp0.cache\huggingface"
set "HUGGINGFACE_HUB_CACHE=%~dp0.cache\huggingface\hub"
set "HF_HUB_CACHE=%~dp0.cache\huggingface\hub"
set "TRANSFORMERS_CACHE=%~dp0.cache\huggingface\transformers"
set "XDG_CACHE_HOME=%~dp0.cache"
set "HF_HUB_DISABLE_XET=1"

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    echo UtaSyncNext .venv was not found.
    echo Please run:
    echo   powershell -ExecutionPolicy Bypass -File tools\bootstrap_venv.ps1
    pause
    exit /b 1
)

"%PYTHON_EXE%" app.py
