@echo off
rem Starts Tracen Replay on this machine and opens it in the browser.
rem Everything it keeps (accounts, recordings, reports) lives under
rem %LOCALAPPDATA%\TracenReplay; nothing leaves the machine.
setlocal
set "HERE=%~dp0"
set "DATA=%LOCALAPPDATA%\TracenReplay"
if not exist "%DATA%" mkdir "%DATA%"
set "PATH=%HERE%ffmpeg;%PATH%"
start "" "http://127.0.0.1:8765/"
"%HERE%tracen.exe" -data "%DATA%" -python "%HERE%python\python.exe" -workdir "%HERE%analyzer" -model-dir "%HERE%models" -ffmpeg "%HERE%ffmpeg\ffmpeg.exe" -ffprobe "%HERE%ffmpeg\ffprobe.exe" -workers 3 -dense-workers 2 -max-recordings 0 -max-recording-gb 0 -max-storage-gb 0 -max-active-per-user 0 -daily-per-user 0 -daily-total 0 -recording-retention 0 %*
endlocal
