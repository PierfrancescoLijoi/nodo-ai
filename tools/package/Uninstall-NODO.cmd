@echo off
rem Removes the program and its Start menu entry. Meetings and models in %LOCALAPPDATA%\NODO stay.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" -Uninstall
pause
