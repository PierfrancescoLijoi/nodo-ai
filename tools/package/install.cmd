@echo off
rem Entry point of the self-extracting installer (see build_installer.ps1).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %* > "%TEMP%\nodo-setup.log" 2>&1
type "%TEMP%\nodo-setup.log"
