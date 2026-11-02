@echo off
rem 把下面的 PYTHON 改成装好 Seed-VC 的那个环境的 python.exe
set PYTHON=third_party\seed-vc\env\Scripts\python.exe
set PYTHONUTF8=1
rem 模型都下载过之后打开这行，启动就不会去联网检查（网络不好时会卡住）
rem set HF_HUB_OFFLINE=1
cd /d %~dp0
"%PYTHON%" -m qqbot_music serve
pause
