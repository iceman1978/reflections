@echo off
rem Starts a TEST copy of In His Steps (the Christian edition) on this computer, at http://localhost:5062
rem   - its own throwaway data in data\test-christian (never your journal, never the live sites)
rem   - a "sign in as..." box instead of Google, so you can be yourself or a pretend family member
rem   - reflections and prompts are real (they use the API key in .env), with the web's daily limits
rem Close this window to stop it. Delete data\test-christian to start the test copy from scratch.
cd /d "%~dp0"
title In His Steps - web test copy
set "EDITION=christian"
set "JOURNAL_DATA_DIR=%~dp0data\test-christian"
set "JOURNAL_PORT=5062"
set "JOURNAL_TEST_LOGIN=1"
set "GOOGLE_CLIENT_ID=test-only"
set "ALLOWED_EMAILS=you@example.com, mum@example.com, dad@example.com"
set "ADMIN_EMAILS=you@example.com"
rem No daily limit while testing (0 = no limit). PUT BACK to 3 when done testing.
set "REFLECTIONS_PER_DAY=0"
set "PROMPTS_PER_DAY=10"
start "" cmd /c "timeout /t 4 /nobreak >nul & start http://localhost:5062"
uv run --quiet python app.py
if errorlevel 1 (
  echo.
  echo The test copy stopped. See the message above, or data\test-christian\logs\journal.log for details.
  pause
)
