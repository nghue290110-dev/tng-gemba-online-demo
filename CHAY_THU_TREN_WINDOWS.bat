@echo off
chcp 65001 >nul
title TNG Gemba Walk DEMO
set /p ADMIN_EMAIL=Nhap email Admin (vi du: admin@example.com): 
set /p ADMIN_PASSWORD=Nhap mat khau Admin manh (chi dung khi chay lan dau): 
set COOKIE_SECURE=0
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo Khong cai duoc thu vien. Vui long cai Python 3.11+.
  pause
  exit /b 1
)
echo.
echo Mo trinh duyet: http://localhost:8000
python -m uvicorn app.server:app --host 0.0.0.0 --port 8000
pause
