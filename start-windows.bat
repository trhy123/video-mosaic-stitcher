@echo off
where pythonw >nul 2>nul
if errorlevel 1 (
  echo Python was not found in PATH. Please install Python with Tkinter.
  pause
  exit /b 1
)
start "" pythonw "%~dp0video_mosaic.py"
