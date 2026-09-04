@echo off
rem Savasan IHA YKI - Windows baslatici
rem Kullanim: cift tikla veya "baslat.bat --sim" / "baslat.bat --kaynak rosbridge"
cd /d "%~dp0"
python -m yki.main %*
if errorlevel 1 pause
