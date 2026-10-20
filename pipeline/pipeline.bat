@echo off
rem Convert the game models and render them, with a progress bar. Run from anywhere:
rem   pipeline.bat                everything the wiki needs (resumes where it left off)
rem   pipeline.bat AKM            one class, or every class containing the text
rem   pipeline.bat -- --fresh     any run.py option; after a second -- Blender options, e.g. -- -- --engine CYCLES --gpu
rem Edit the two paths below if the render folder or Blender live elsewhere.
setlocal
set "RENDER_ROOT=F:\PureZ\PureZ-render"
set "BLENDER_EXE=C:\Program Files\Blender Foundation\Blender 5.1\blender.exe"

if "%~1"=="" (
    python "%~dp0run.py" --render "%RENDER_ROOT%" --blender "%BLENDER_EXE%"
) else if "%~1"=="--" (
    shift
    python "%~dp0run.py" --render "%RENDER_ROOT%" --blender "%BLENDER_EXE%" %*
) else (
    python "%~dp0run.py" --render "%RENDER_ROOT%" --blender "%BLENDER_EXE%" --only "%~1"
)
exit /b %ERRORLEVEL%
