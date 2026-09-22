@echo off
rem fairy v2 - DEBUG launcher (keeps a console window so you can see errors).
rem Use this only if start_fairy.vbs seems to do nothing.
rem Pure ASCII on purpose; %~dp0 avoids embedding the Chinese path.
chcp 65001 >nul
cd /d "%~dp0"
rem Locate python.exe WITHOUT hard-coding a path:
rem   %FAIRY_PYTHON%  ->  python_path.txt next to this script  ->  PATH  ->  standard dirs
set PY=
if exist "%~dp0python_path.txt" set /p PY=<"%~dp0python_path.txt"
if not "%FAIRY_PYTHON%"=="" if exist "%FAIRY_PYTHON%" set PY=%FAIRY_PYTHON%
if "%PY%"=="" for %%I in (python.exe) do if not "%%~$PATH:I"=="" set PY=%%~$PATH:I
if "%PY%"=="" for /d %%D in ("%LOCALAPPDATA%\Programs\Python\*") do if exist "%%D\python.exe" set PY=%%D\python.exe
if "%PY%"=="" for /d %%D in ("%ProgramFiles%\Python*") do if exist "%%D\python.exe" set PY=%%D\python.exe
if not exist "%PY%" (
  echo python.exe not found: %PY%
  pause
  exit /b 1
)
echo === fairy pet (debug, console visible) ===
echo dir: %CD%
"%PY%" fairy_pet.py
echo.
echo === exited, errorlevel=%errorlevel% ===
echo (if you saw a traceback above, send it to the assistant)
pause
