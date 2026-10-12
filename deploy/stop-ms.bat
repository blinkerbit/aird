@echo off
setlocal
REM Stop the local mesh: gateway plus every service holding a mesh port.
taskkill /f /im caddy.exe >nul 2>nul

for %%P in (8000 8001 8002 8004 8005 8006 8007) do (
  for /f "tokens=5" %%I in ('netstat -ano ^| findstr /c:"127.0.0.1:%%P " ^| findstr /c:"LISTENING"') do (
    taskkill /f /pid %%I >nul 2>nul
  )
)

echo Stopped aird microservices and caddy.
endlocal
