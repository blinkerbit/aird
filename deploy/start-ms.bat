@echo off
setlocal
REM Start the full local mesh using the existing uv-managed .venv.
if not defined AIRD_MESH_SECRET set AIRD_MESH_SECRET=dev-mesh-secret
if not defined AIRD_JWT_SECRET set AIRD_JWT_SECRET=dev-jwt-secret-change-in-production
if not defined AIRD_DEV_OPEN_AUTH set AIRD_DEV_OPEN_AUTH=1

set AIRD_AUTH_HOST=127.0.0.1
set AIRD_AUTH_PORT=8000
set AIRD_BROWSE_HOST=127.0.0.1
set AIRD_BROWSE_PORT=8001
set AIRD_TRANSFER_HOST=127.0.0.1
set AIRD_TRANSFER_PORT_HTTP=8002
set AIRD_SEARCH_HOST=127.0.0.1
set AIRD_SEARCH_PORT=8004
set AIRD_SHARES_HOST=127.0.0.1
set AIRD_SHARES_PORT=8005
set AIRD_P2P_HOST=127.0.0.1
set AIRD_P2P_PORT=8006
set AIRD_ADMIN_HOST=127.0.0.1
set AIRD_ADMIN_PORT=8007

where uv >nul 2>nul
if errorlevel 1 (
  echo uv was not found on PATH.
  exit /b 1
)

set "CADDY_EXE=caddy"
where caddy >nul 2>nul
if errorlevel 1 set "CADDY_EXE=D:\caddy_2.11.4_windows_amd64\caddy.exe"
if not exist "%CADDY_EXE%" (
  where "%CADDY_EXE%" >nul 2>nul
  if errorlevel 1 (
    echo Caddy was not found on PATH or at D:\caddy_2.11.4_windows_amd64\caddy.exe
    exit /b 1
  )
)

REM A leftover Caddy keeps :2019/:8080 and makes the new gateway config silently fail to load.
taskkill /f /im caddy.exe >nul 2>nul

for %%P in (8000 8001 8002 8004 8005 8006 8007) do (
  netstat -ano | findstr /r /c:"LISTENING" | findstr /c:"127.0.0.1:%%P " >nul 2>nul
  if not errorlevel 1 (
    echo Port %%P is already in use. Run deploy\stop-ms.bat first.
    exit /b 1
  )
)

start "aird-auth :8000" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.auth_service"
start "aird-browse :8001" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.browse_service"
start "aird-transfer :8002" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.transfer"
start "aird-search :8004" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.search_service"
start "aird-shares :8005" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.shares_service"
start "aird-p2p :8006" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.p2p_service"
start "aird-admin :8007" cmd /k "cd /d %~dp0.. && uv run python -m aird.ms.admin_service"
start "aird-caddy :8080" cmd /k "cd /d %~dp0.. && ""%CADDY_EXE%"" run --config deploy\Caddyfile"

echo Started auth:8000 browse:8001 transfer:8002 search:8004 shares:8005 p2p:8006 admin:8007 caddy:8080
echo App:    http://localhost:8080/
echo Health: http://localhost:8080/health
echo Also:   http://127.0.0.1:8080/
endlocal
