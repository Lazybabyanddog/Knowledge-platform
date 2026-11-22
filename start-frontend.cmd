@echo off
rem 双击启动前端：Vite 开发服务器，窗口保持并实时输出日志
chcp 65001 >nul
cd /d "%~dp0"
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-frontend.ps1" %*
if errorlevel 1 pause
