@echo off
rem BuildBot setup for Windows. Double-click this file.
rem
rem It runs install.ps1 with -ExecutionPolicy Bypass. That setting lasts for
rem this one window only: nothing about how your PC runs scripts is changed.
rem Any options are passed on, for example:  install.cmd -Port 8100
rem Set PUFFPAI_NO_PAUSE=1 to skip the "press any key" at the end.
setlocal
title BuildBot setup
cd /d "%~dp0"
set "PUFFPAI_FROM_CMD=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" echo BuildBot setup is finished.
if not "%RC%"=="0" echo BuildBot setup stopped before the end. The lines above say why.
if not "%PUFFPAI_NO_PAUSE%"=="1" pause
exit /b %RC%
