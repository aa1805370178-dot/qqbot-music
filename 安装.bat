@echo off
chcp 65001 >nul
rem One-click install: check PC -> local Python in env\ -> download models -> self-test -> open the admin page.
rem Safe to run again: finished steps are skipped. Does not touch your existing Python / conda / CUDA.
rem Keep this file ASCII-only: cmd mis-reads lines of UTF-8 Chinese text in .bat files after chcp.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1" %*
echo.
pause
