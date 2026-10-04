@echo off
rem Fetch new r/CrackWatch posts (public RSS), rebuild the site, publish it.
rem Run by the Windows task "Crack Watch fetcher" every 2 hours.
rem Publishing only happens once a GitHub remote named "origin" is set up.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist data mkdir data
%PY% -u fetch.py >> "data\update.log" 2>&1 || goto :eof
%PY% -u builder\build.py >> "data\update.log" 2>&1 || goto :eof
git remote get-url origin >nul 2>&1 || goto :eof
git add docs
git diff --cached --quiet && goto :eof
git commit -q -m "Update games" >> "data\update.log" 2>&1
git push -q origin main >> "data\update.log" 2>&1
