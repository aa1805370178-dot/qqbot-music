@echo off
chcp 65001 >nul
rem 双击安装：检测电脑环境 → 在本文件夹的 env\ 里装独立的 Python 和依赖 → 下载模型 → 自检 → 打开管理页。
rem 可以重复双击：装好的部分会跳过，中断了接着装。不改动电脑上已有的 Python / conda / CUDA。
cd /d %~dp0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install\install.ps1" %*
echo.
pause
