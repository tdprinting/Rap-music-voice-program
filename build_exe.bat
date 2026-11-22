@echo off
rem Builds a standalone Windows app: dist\SIB Studio Mixer\SIB Studio Mixer.exe
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call "Install SIB Studio.bat"
".venv\Scripts\python.exe" -m pip install pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
  --name "SIB Studio Mixer" ^
  --icon assets\sib.ico ^
  --add-data "assets;assets" ^
  --add-data "sib_studio/data;sib_studio/data" ^
  --collect-all _soundfile_data ^
  --collect-all _sounddevice_data ^
  --collect-submodules scipy.signal ^
  --collect-submodules scipy.ndimage ^
  run_sib.py
echo.
echo Built: dist\SIB Studio Mixer\SIB Studio Mixer.exe
echo Zip the whole "dist\SIB Studio Mixer" folder to share it with your friend.
pause
