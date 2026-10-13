@echo off
setlocal
python "%~dp0gdopen.py" %*
exit /b %ERRORLEVEL%
