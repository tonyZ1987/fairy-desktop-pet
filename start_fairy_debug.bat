@echo off
rem fairy v2 - DEBUG launcher (keeps a console window so you can see errors).
rem Use this only if start_fairy.vbs seems to do nothing.
rem Pure ASCII on purpose; %~dp0 avoids embedding the Chinese path.
chcp 65001 >nul
cd /d "%~dp0"
rem ---- locate python.exe (no hard-coded machine path) ----
rem   %FAIRY_PYTHON%  ->  python_path.txt beside this script  ->  managed venv
rem   ->  other managed python  ->  PATH  ->  common install folders
set PY=
if exist "%~dp0python_path.txt" set /p PY=<"%~dp0python_path.txt"
if "%PY%"=="" if exist "%~dp0..\python_path.txt" set /p PY=<"%~dp0..\python_path.txt"
if not "%FAIRY_PYTHON%"=="" set PY=%FAIRY_PYTHON%
if not "%PY%"=="" if exist "%PY%\python.exe" set PY=%PY%\python.exe
if /i "%PY:~-11%"=="pythonw.exe" set PY=%PY:~0,-11%python.exe
if "%PY%"=="" for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\envs\*") do if exist "%%D\Scripts\python.exe" set PY=%%D\Scripts\python.exe
if "%PY%"=="" for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%D\python.exe" set PY=%%D\python.exe
if "%PY%"=="" for %%I in (python.exe) do if not "%%~$PATH:I"=="" set PY=%%~$PATH:I
if "%PY%"=="" for /d %%D in ("%LOCALAPPDATA%\Programs\Python\*") do if exist "%%D\python.exe" set PY=%%D\python.exe
if "%PY%"=="" for /d %%D in ("%ProgramFiles%\Python*") do if exist "%%D\python.exe" set PY=%%D\python.exe
if not exist "%PY%" (
  echo [x] python.exe not found.
  echo     Put the full path of python.exe ^(or its folder^) into python_path.txt
  echo     next to this script, then run again.
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
