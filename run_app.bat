@echo off
setlocal ENABLEDELAYEDEXPANSION

REM =========================================
REM 一鍵安裝 / 啟動（穩定版）
REM 檔名固定：run_app.bat（ANSI 編碼）
REM 由 project_launcher.py 自動產生
REM =========================================

pushd "%~dp0"

echo( =========================================
echo(   一鍵安裝 / 啟動（穩定版）
echo( =========================================
echo(.

echo( [1/6] 檢查 Python...
where python >nul 2>&1
if errorlevel 1 (
  echo(.
  echo( 找不到 Python，請先安裝 Python 3.10 以上。
  echo( 下載網址：https://www.python.org/downloads/
  echo( 安裝時請勾選 Add Python to PATH
  echo(.
  pause
  popd
  exit /b 1
)

echo( [2/6] 建立虛擬環境（.venv）...
if not exist ".venv\Scripts\python.exe" (
  python -m venv ".venv"
  if errorlevel 1 (
    echo(.
start "Backend Worker" cmd /k ""%PYEXE%" -m app.backend_daemon.worker 1>>"logs\backend_worker.log" 2>>&1"
    echo( 無法建立虛擬環境。可能原因：權限不足或防毒阻擋。
    echo( 建議：右鍵 run_app.bat → 以系統管理員身分執行
    echo(.
    pause
    popd
    exit /b 1
  )
)

set "PYEXE=%~dp0.venv\Scripts\python.exe"

echo( [3/6] 自動檢查/修正 + 安裝依賴...
"%PYEXE%" "project_launcher.py"
if errorlevel 1 (
  echo(.
  echo( [ERROR] 自動檢查/修正失敗，請看上方輸出訊息。
  echo(.
  pause
  popd
  exit /b 1
)

echo( [4/6] 啟動後端（uvicorn）...
echo( 啟動時間: %DATE% %TIME%
if not exist "logs" mkdir "logs"
start "Backend" cmd /k ""%PYEXE%" -m uvicorn xxx:yyy --host 127.0.0.1 --port 8000 --log-level info 1>>"logs\backend.log" 2>>&1"


echo( [5/6] 前端未偵測到（沒有 package.json，且找不到 dist/build/index.html），略過前端啟動。
echo( [5/6] 開啟Slide Manager圖形介面...
start "Slide Manager GUI" cmd /k "cd /d \"%PROJECT_DIR%\" && call \"%VENV_DIR%\Scripts\activate.bat\" && python -m app.main"



echo(.
echo( =========================================
echo( 啟動完成。要停止服務請關閉 Backend / Frontend 視窗。
echo( 若有錯誤，請將錯誤訊息回傳給 AI 助手。
echo( =========================================
echo(.
pause
popd
endlocal
