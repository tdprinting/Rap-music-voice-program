@echo off
title SIB Studio Mixer - Install
cd /d "%~dp0"
echo.
echo   ============================================
echo     SIB Studio Mixer  -  Shadows in the Booth
echo   ============================================
echo.
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install Python 3.11 or newer from https://www.python.org/downloads/
  echo IMPORTANT: tick "Add python.exe to PATH" in the installer, then run this file again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating private Python environment...
  %PY% -m venv .venv
)
echo Installing audio engine and interface (first time takes a few minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Install failed - check your internet connection and try again.
  pause
  exit /b 1
)
echo.
echo Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\SIB Studio Mixer.lnk');" ^
  "$s.TargetPath='%~dp0.venv\Scripts\pythonw.exe'; $s.Arguments='-m sib_studio'; $s.WorkingDirectory='%~dp0';" ^
  "$s.IconLocation='%~dp0assets\sib.ico'; $s.Save()"
where ffmpeg >nul 2>nul
if errorlevel 1 (
  echo.
  echo Optional: install FFmpeg for MP3/M4A import and MP3 export:
  echo     winget install ffmpeg
)
echo.
echo Done! Start SIB Studio Mixer from the desktop shortcut or "SIB Studio Mixer.bat".
pause
