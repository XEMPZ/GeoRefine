# 第三方组件许可

本软件（GeoRefine）以 MIT 许可发布。运行时与打包分发涉及以下第三方组件，
各自遵循其原始许可证：

本软件依赖的第三方组件（各自遵循其许可证）：

* PySide6 / Qt for Python — LGPL-3.0（动态链接，未修改）
* NumPy — BSD-3-Clause
* Pillow — MIT-CMU
* piexif — MIT
* ezdxf — MIT
* pyshp — MIT
* openpyxl — MIT
* tifffile — BSD-3-Clause
* laspy — BSD-2-Clause
* pyproj / PROJ — MIT / X11（仅用于读取 LAS 头里的坐标系定义）
* pywin32 — PSF（Windows 上用于 AutoCAD COM 与 PGDB 读取）
* OpenSceneGraph — LGPL-2.1 with wxWidgets exception（OSGB 转换器，用户自行构建）


---

## 分发说明

- 本软件的 Windows 免安装版（Release 中的 zip）内含上述组件的二进制形式。
- PySide6 / Qt 以 **LGPL-3.0 动态链接**方式使用，未做修改；
  如需替换为自行编译的版本，可直接替换 `_internal/` 下对应的 DLL。
- `imagecodecs` — BSD-3-Clause（EGM 系列 GeoTIFF 格网的浮点预测器解码需要）。
- 大地水准面格网数据（EGM96 / EGM2008）由用户自行获取，其再分发条款请自行确认；
  仓库内仅内置 `egm96_15.gtx`（全球 EGM96，公有领域数据）。
