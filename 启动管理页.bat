@echo off
chcp 65001 >nul
rem 双击打开管理页：看曲库、学新歌、试唱、换音色、填 B 站 cookie。初始密码 123456，登录后请改掉。
cd /d %~dp0
rem 找 Python：一键安装的 env\venv → 以前手动装在 Seed-VC 里的环境 → PATH 里的 python
set PYTHON=env\venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=third_party\seed-vc\env\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
set PYTHONUTF8=1
rem 一键安装自检通过后就不再联网检查模型（网络不好时不会卡住）
if exist env\install_ok.json set HF_HUB_OFFLINE=1
echo 管理页启动后会自动打开浏览器；关掉这个窗口管理页就停了（唱歌服务不受影响）
"%PYTHON%" -m qqbot_music admin --open
pause
