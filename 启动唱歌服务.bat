@echo off
chcp 65001 >nul
rem 只启动唱歌服务（给机器人插件调）。一般不用手动开：插件和管理页连不上会自己拉起。
cd /d %~dp0
rem 找 Python：一键安装的 env\venv → 以前手动装在 Seed-VC 里的环境 → PATH 里的 python
set PYTHON=env\venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=third_party\seed-vc\env\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
set PYTHONUTF8=1
rem 一键安装自检通过后就不再联网检查模型（网络不好时不会卡住）
if exist env\install_ok.json set HF_HUB_OFFLINE=1
"%PYTHON%" -m qqbot_music serve
pause
