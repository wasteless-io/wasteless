@echo off
rem WasteLess - desinstallation sous Windows natif (voir uninstall.ps1).
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %*
exit /b %ERRORLEVEL%
