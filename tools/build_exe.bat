@echo off
rem ============================================================
rem  GeoRefine v1.0 打包脚本（PyInstaller --onedir --windowed）
rem  产物: dist\GeoRefine\GeoRefine.exe（连同 _internal 依赖目录）
rem  前置: pip install -r requirements.txt pyinstaller
rem  说明: models\egm96_15.gtx 已随包内置；EGM2008 tif 首次使用
rem        时用 tools\download_geoid_models.py 下载或导入局部格网
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0.."

where pyinstaller >nul 2>nul || (echo [!] 未检测到 pyinstaller，正在安装... && python -m pip install pyinstaller)

python -m PyInstaller --noconfirm --clean ^
  --onedir --windowed --name GeoRefine ^
  --add-data "%~dp0..\models;models" ^
  --hidden-import piexif ^
  --collect-submodules ezdxf ^
  "%~dp0..\main.py"

if errorlevel 1 (
  echo [x] 打包失败，请检查上方错误信息
  exit /b 1
)
echo [ok] 打包完成: dist\GeoRefine\GeoRefine.exe
echo      分发时整个 dist\GeoRefine 目录一起拷贝。
exit /b 0
