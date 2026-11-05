@echo off
rem WasteLess - installation sous Windows natif (voir install.ps1).
rem Passe par -ExecutionPolicy Bypass : la politique par defaut de Windows
rem (Restricted) interdit sinon d'executer install.ps1 directement.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
exit /b %ERRORLEVEL%
