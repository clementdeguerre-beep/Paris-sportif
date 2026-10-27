@echo off
rem Routine planifiee (Planificateur de taches) : releve des cotes puis scan. Journal dans logs\routine.log.
cd /d "%~dp0.."
set PYTHONUTF8=1
if not exist logs mkdir logs
echo === %date% %time% === >> logs\routine.log
.venv\Scripts\tennisvalue.exe fetch-odds >> logs\routine.log 2>&1 && .venv\Scripts\tennisvalue.exe scan >> logs\routine.log 2>&1
