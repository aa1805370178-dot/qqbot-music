@echo off
chcp 65001 >nul
rem Open the admin page (library, learn songs, try singing, voice, Bilibili cookie). Default password 123456.
rem Keep this file ASCII-only: cmd mis-reads lines of UTF-8 Chinese text in .bat files after chcp.
cd /d "%~dp0"
rem Python: one-click env\venv -> old manual install inside Seed-VC -> python on PATH
set PYTHON=env\venv\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=third_party\seed-vc\env\Scripts\python.exe
if not exist "%PYTHON%" set PYTHON=python
set PYTHONUTF8=1
rem After the one-click self-test passed, stay offline (no model update checks, no hang on bad network)
if exist env\install_ok.json set HF_HUB_OFFLINE=1
"%PYTHON%" -m qqbot_music admin --open
pause
