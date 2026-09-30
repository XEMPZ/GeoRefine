# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：GeoRefine 免安装版（Windows）。

构建： python -m PyInstaller GeoRefine.spec --noconfirm
产物： dist/GeoRefine/  （整目录拷走即可运行，无需 Python）

要点：
  · onedir（目录模式）：启动比 onefile 快得多，且 Qt 插件不会每次解压。
  · 只打 app/ 里的代码；models/ params/ logs/ 放在 exe 旁边由用户自己放，
    这样格网模型和参数库重启后仍在（见 app/__init__.py::app_root）。
  · 排除用不到的 Qt 模块，显著减小体积。
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# 只收项目自身代码；数据文件（格网/参数/手册）随包外置
hiddenimports = [
    "app.common.params", "app.common.io_csv",
    "app.common.logutil", "app.logic.apply_params", "app.logic.cp_methods",
    "app.logic.space_points", "app.logic.file_jobs", "app.logic.photo_spec",
    "app.logic.accuracy", "app.core.geoid", "app.core.gk_engine",
    "app.core.projection", "app.core.transform2d",
    "app.osgb.flash", "app.osgb.transform", "app.pointcloud.las_io",
    "app.pointcloud.las_transform", "app.pointcloud.las_job",
]
hiddenimports += collect_submodules("laspy")
# 注：pyproj 只在开发期对标工具 tools/verify_models.py 里用，运行时不依赖，
# 故**不打进发行包**（此前误用 collect_submodules 打进去了，约多 10 MB）。
# imagecodecs 是 EGM 系列 GeoTIFF 格网的必需依赖（浮点预测器解码）。
# 它是一个"薄壳 + 几十个独立 .pyd 编解码器"的包，PyInstaller 默认只抓到
# imagecodecs/_shared.pyd，其余全漏 —— 打包版读 tif 会失败。
# 因此显式收集全部子模块与动态库。


excludes = [
    # 用不到的 Qt 组件（体积大头）
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtNetworkAuth", "PySide6.QtPositioning", "PySide6.QtSql",
    "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
    "PySide6.QtStateMachine", "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSvgWidgets",
    # 科学计算里不用到的
    "matplotlib", "pandas", "scipy", "IPython", "notebook", "pytest", "tkinter",
    "PyQt5", "PyQt6", "PySide2",
]

# imagecodecs 必须用 collect_all：它由 __init__.py + 几十个独立 .pyd 编解码器组成，
# collect_dynamic_libs 只搬 .pyd 不带 __init__.py，会变成"命名空间包"，
# 结果 import imagecodecs 不报错但拿不到任何编解码器，读 tif 仍然失败。
_ic_datas, _ic_bins, _ic_hidden = collect_all("imagecodecs")
hiddenimports += _ic_hidden

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=_ic_bins,
    datas=_ic_datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeoRefine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 无控制台窗口（GUI 程序）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GeoRefine",
)
