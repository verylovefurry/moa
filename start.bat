@echo off
cd /d "%~dp0"
if exist "%LOCALAPPDATA%\Programs\Moa\Moa.exe" (
  start "" "%LOCALAPPDATA%\Programs\Moa\Moa.exe"
  exit /b
)
if exist "%~dp0release\Moa.exe" (
  start "" "%~dp0release\Moa.exe"
  exit /b
)
set "MOA_PYTHONW=%LOCALAPPDATA%\Programs\Python\Python311\pythonw.exe"
if exist "%MOA_PYTHONW%" (
  start "" "%MOA_PYTHONW%" "%~dp0desktop.py"
  exit /b
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0desktop.py"
  exit /b
)
python desktop.py
if errorlevel 1 pause
