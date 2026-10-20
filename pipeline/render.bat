@echo off
rem Render the converted models headless with Blender. Run from anywhere:
rem   render.bat                every model in pipeline\work (resumes: existing images are skipped)
rem   render.bat AKM            classes whose name contains AKM
rem   render.bat -- --engine CYCLES --gpu --limit 5    any blender_render.py options verbatim
rem Set BLENDER_EXE below if Blender lives elsewhere. Output: media\renders and media\models in the wiki repo,
rem log: pipeline\work\render.log.
setlocal

set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"
set "WIKI=%~dp0.."
set "RENDER_SCRIPT=%~dp0blender_render.py"
set "WORK=%WIKI%\pipeline\work"
set "OUT=%WIKI%\media"

if not exist "%BLENDER_EXE%" (
    echo Blender was not found at "%BLENDER_EXE%". Edit BLENDER_EXE in %~nx0.
    exit /b 1
)
if not exist "%WORK%" (
    echo Nothing to render: "%WORK%" is missing. Run: python "%WIKI%\pipeline\layout.py" --render F:\PureZ\PureZ-render
    exit /b 1
)

if "%~1"=="" (
    "%BLENDER_EXE%" --background --factory-startup --python "%RENDER_SCRIPT%" -- --work "%WORK%" --out "%OUT%" --resume
) else if "%~1"=="--" (
    shift
    "%BLENDER_EXE%" --background --factory-startup --python "%RENDER_SCRIPT%" -- --work "%WORK%" --out "%OUT%" %*
) else (
    "%BLENDER_EXE%" --background --factory-startup --python "%RENDER_SCRIPT%" -- --work "%WORK%" --out "%OUT%" --resume --only "%~1"
)

exit /b %ERRORLEVEL%
