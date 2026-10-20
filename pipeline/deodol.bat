@echo off
rem Debinarise the render set with DeODOL v54, falling back to v53 per file. Edit the paths if they move.
set "ROOT=%~1"
if "%ROOT%"=="" set "ROOT=F:\PureZ\PureZ-render"
python "%~dp0deodol.py" "%ROOT%" --exe "C:\Users\Jade\Desktop\Deodol54.exe" --fallback "C:\Users\Jade\Desktop\DeODOL53.exe"
pause
