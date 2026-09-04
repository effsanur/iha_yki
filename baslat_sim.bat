@echo off
rem Donanimsiz tam deneme: sahte yarisma sunucusu + benzetim ucusuyla YKI acar.
rem YKI'de sunucu adresi http://127.0.0.1:5000 , kadi=takimkadi sifre=takimsifresi
cd /d "%~dp0"
start "Sahte Yarisma Sunucusu" cmd /k python sim_sunucu.py --hss-baslangic 30
python -m yki.main --sim
if errorlevel 1 pause
