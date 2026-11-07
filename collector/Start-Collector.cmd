@echo off
setlocal
cd /d "%~dp0"
if exist VionCollectorGUI.exe (
  start "" "%~dp0VionCollectorGUI.exe"
  exit /b
)
if exist VionCollector.exe (
  VionCollector.exe --host 0.0.0.0 --port 8790
) else (
  py -3 collector.py --host 0.0.0.0 --port 8790
)
pause
