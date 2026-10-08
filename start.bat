@echo off
rem Starts the journal. Double-click this file, or run start.bat in a terminal.
cd /d "%~dp0"
title Journal
uv run --quiet python app.py
if errorlevel 1 (
  echo.
  echo The journal stopped. See the message above, or logs\journal.log for details.
  pause
)
