@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo First run - installing...
  call "Install SIB Studio.bat"
)
start "" ".venv\Scripts\pythonw.exe" -m sib_studio %*
