@echo off
rem Starts NODO AI: the local engine and its window.
setlocal
set HF_HUB_OFFLINE=1
"%~dp0services\api\.venv\Scripts\python.exe" -m nodo_api.desktop %*
