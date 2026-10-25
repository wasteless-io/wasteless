@echo off
rem WasteLess CLI shim for native Windows: runs wasteless.ps1 without
rem requiring a change of the machine's PowerShell execution policy.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0wasteless.ps1" %*
exit /b %ERRORLEVEL%
