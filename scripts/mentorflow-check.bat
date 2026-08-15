@echo off
setlocal EnableExtensions

rem This file is copied to <package>\SELF-CHECK and also runs from scripts\.
set "SELF_CHECK_DIR=%~dp0"
set "PACKAGE_ROOT=%SELF_CHECK_DIR%.."
set "HELPER=%SELF_CHECK_DIR%MentorFlowSelfCheck.exe"
set "VERIFIER=%SELF_CHECK_DIR%verify_windows_package.py"
if exist "%HELPER%" (
  "%HELPER%" verify --package-root "%PACKAGE_ROOT%" --data-root "E:\MentorFlowData" %*
  set "RESULT=%ERRORLEVEL%"
  goto :result
)

rem Python fallback is intentionally source-tree-only.  A shipped package
rem must carry the frozen helper instead of relying on a user's Python.
if not exist "%SELF_CHECK_DIR%..\desktop_entry.py" goto :missing_helper
if not exist "%VERIFIER%" set "VERIFIER=%SELF_CHECK_DIR%..\scripts\verify_windows_package.py"
if not exist "%VERIFIER%" (
  echo MentorFlow source-tree self-check verifier is missing: "%VERIFIER%" 1>&2
  exit /b 2
)
if defined MENTORFLOW_PYTHON if exist "%MENTORFLOW_PYTHON%" (
  "%MENTORFLOW_PYTHON%" "%VERIFIER%" verify --package-root "%PACKAGE_ROOT%" --data-root "E:\MentorFlowData" %*
  set "RESULT=%ERRORLEVEL%"
  goto :result
)
where py >nul 2>&1
if not errorlevel 1 (
  py -3 "%VERIFIER%" verify --package-root "%PACKAGE_ROOT%" --data-root "E:\MentorFlowData" %*
  set "RESULT=%ERRORLEVEL%"
  goto :result
)
where python >nul 2>&1
if not errorlevel 1 (
  python "%VERIFIER%" verify --package-root "%PACKAGE_ROOT%" --data-root "E:\MentorFlowData" %*
  set "RESULT=%ERRORLEVEL%"
  goto :result
)
echo Python 3 is required for the read-only SQLite and SHA-256 self-check. 1>&2
exit /b 9009
:missing_helper
echo MentorFlowSelfCheck.exe is missing from this package. Rebuild the distributable or run this BAT from the source tree. 1>&2
exit /b 2
:result
if "%RESULT%"=="0" echo MentorFlow package self-check passed. No user data was changed.
endlocal & exit /b %RESULT%
