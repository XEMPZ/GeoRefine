@echo off
rem ============================================================
rem  GeoRefine 打包脚本（PyInstaller，按 GeoRefine.spec 构建）
rem  产物: dist\GeoRefine\  （整目录分发，免安装、无需 Python）
rem  前置: pip install -r requirements.txt pyinstaller
rem  说明: 打包配置与排除项都在 GeoRefine.spec 里，改那里而不是这里。
rem        models\ params\ 不打进包内，放在 exe 旁边由用户自备。
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0.."

where pyinstaller >nul 2>nul || (echo [!] 未检测到 pyinstaller，正在安装... && python -m pip install pyinstaller)

python -m PyInstaller GeoRefine.spec --noconfirm --clean

if errorlevel 1 (
  echo [x] 打包失败，请检查上方错误信息
  exit /b 1
)
echo [ok] 打包完成: dist\GeoRefine\GeoRefine.exe
echo      分发时整个 dist\GeoRefine 目录一起拷贝。
echo      发布前可运行 tools\make_release.py 生成免安装版压缩包。
exit /b 0
