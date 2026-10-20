@echo off
rem Render the converted models headless with Blender. Run from anywhere:
rem   render.bat            every model in pipeline\work
rem   render.bat AKM        one class only
rem Set BLENDER_EXE below if Blender lives elsewhere. Output: media\renders and media\models in the wiki repo.
setlocal

set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
set "WIKI=%~dp0.."
set "RENDER_SCRIPT=%~dp0blender_render.py"
set "WORK=%WIKI%\pipeline\work"
set "OUT=%WIKI%\media"

if not exist "%BLENDER_EXE%" (
    echo Blender was not found at "%BLENDER_EXE%".
    exit /b 1
)
if not exist "%WORK%" (
    echo Nothing to render: "%WORK%" is missing. Run layout.py first.
    exit /b 1
)

if "%~1"=="" (
    "%BLENDER_EXE%" --background --python "%RENDER_SCRIPT%" -- --work "%WORK%" --out "%OUT%" --gpu
) else (
    "%BLENDER_EXE%" --background --python "%RENDER_SCRIPT%" -- --work "%WORK%" --out "%OUT%" --gpu --only "%~1"
)

exit /b %ERRORLEVEL%
