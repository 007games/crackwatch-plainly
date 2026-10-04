@echo off
rem One-off: adds every old r/CrackWatch post, slowly, then disables its own task.
rem Run by the Windows task "Crack Watch archive backfill" every 30 minutes. See backfill.py.
rem The 2-hourly update.cmd publishes what it adds.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist data mkdir data
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" -u backfill.py >> "data\backfill.log" 2>&1
