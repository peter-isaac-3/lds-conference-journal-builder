@echo off
setlocal
cd /d "%~dp0"
title Conference Journal Maker

rem --- find a working Python (the "py" launcher first, then "python") ---
set "PY="
where py >nul 2>nul && py -3 -c "import sys" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys" >nul 2>nul && set "PY=python"
)
if not defined PY goto :nopython

if exist ".venv\Scripts\python.exe" goto :run

echo.
echo  First-time setup - this takes a minute or two. Please wait...
echo.
%PY% -m venv .venv
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
  rmdir /s /q .venv
  goto :fail
)

:run
".venv\Scripts\python.exe" journal_app.py
echo.
echo  Journal Maker has stopped. You can close this window.
pause
exit /b 0

:nopython
echo.
echo  Python (a free helper program) isn't installed on this computer yet.
echo.
echo   1. A web page is about to open. Click the big yellow "Download Python" button
echo      and run the file it downloads.
echo   2. On the FIRST screen of the installer, tick the box "Add python.exe to PATH",
echo      then click "Install Now".
echo   3. When it finishes, double-click this file again.
echo.
pause
start "" "https://www.python.org/downloads/"
exit /b 1

:fail
echo.
echo  Sorry - setup didn't finish. Please check that you are connected to the internet,
echo  then double-click this file to try again. If it keeps happening, send a photo of
echo  this window to whoever shared Journal Maker with you.
echo.
pause
exit /b 1
