@echo off
setlocal
pushd "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PYTHON%" (
    echo MentorFlow cannot find its shared Python environment:
    echo   %PYTHON%
    echo Ask the project maintainer to finish preparing D:\MentorFlow\.venv, then try again.
    pause
    popd
    exit /b 1
)

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" -B -m refactor.desktop.account_shell_main
    popd
    exit /b 0
)

"%PYTHON%" -B -m refactor.desktop.account_shell_main
set "EXITCODE=%ERRORLEVEL%"

popd
exit /b %EXITCODE%
