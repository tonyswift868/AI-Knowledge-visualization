@echo off
setlocal
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  echo PowerShell 7 (pwsh.exe) was not found on PATH.
  echo Open PowerShell 7 or install the official PowerShell 7 package first.
  exit /b 1
)
pwsh.exe -NoLogo -NoProfile -File "%~dp0run_desktop_pet.ps1" %*
