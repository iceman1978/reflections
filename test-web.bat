@echo off
rem Starts a TEST copy of the web version on this computer, at http://localhost:5061
rem   - its own throwaway data in data\test-web (never your journal, never the live site)
rem   - a "sign in as..." box instead of Google, so you can be yourself or a pretend family member
rem   - reflections and prompts are real (they use the API key in .env), with the web's daily limits
rem Close this window to stop it. Delete data\test-web to start the test copy from scratch.
cd /d "%~dp0"
title A Life Well Lived - web test copy
set "JOURNAL_DATA_DIR=%~dp0data\test-web"
set "JOURNAL_PORT=5061"
set "JOURNAL_TEST_LOGIN=1"
set "GOOGLE_CLIENT_ID=test-only"
set "ALLOWED_EMAILS=you@example.com, mum@example.com, dad@example.com"
set "ADMIN_EMAILS=you@example.com"
set "REFLECTIONS_PER_DAY=3"
set "PROMPTS_PER_DAY=10"
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:5061"
uv run --quiet python app.py
if errorlevel 1 (
  echo.
  echo The test copy stopped. See the message above, or data\test-web\logs\journal.log for details.
  pause
)
