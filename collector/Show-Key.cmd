@echo off
setlocal
cd /d "%~dp0"
if exist VionCollector.exe (
  VionCollector.exe --show-key
) else (
  py -3 collector.py --show-key
)
pause
