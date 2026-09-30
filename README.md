# GeoRefine — 似大地水准面精化与坐标转换软件

**v1.1** · 作者 求道之心 · MIT License · <https://github.com/XEMPZ/GeoRefine>

把 GNSS 大地坐标（B, L, 大地高 H）与工程用的平面坐标（x, y）和正常高 h 打通：
公共点对一键解算转换参数（自动比选最优方法）→ 批量应用到坐标表、CAD 图、SHP 文件，
或直接落到无人机照片 POS、倾斜摄影 OSGB 模型、LAS 点云上。

## 下载与安装

| 方式 | 适用 | 说明 |
|---|---|---|
| **[免安装版](https://github.com/XEMPZ/GeoRefine/releases)** | 只想用，不想装 Python | 下载 zip 解压，双击 `GeoRefine.exe`。**含全部源代码**，遇到 BUG 可直接用任意 AI 编程工具自行修改 |
| 源码运行 | 要改代码/参与开发 | `pip install -r requirements.txt`，项目根 `python main.py`（Python ≥3.13） |

## 功能

| 功能 | 说明 |
|---|---|
| 控制点转换 | 导入公共点对，自动比选四参数/三参数/七参数/七参数+投影+四参数/投影反算链与高程方案，残差逐点回填，参数存库 |
| 参数应用转换 | 用已保存参数批量转换坐标清单，**平面/高程可拆分** |
| 照片 POS 处理 | 无人机照片 EXIF/XMP 椭球高批量换算为正常高并原位写回；自动备份、逐张台账、可回滚 |
| **OSGB 模型转换** | 倾斜摄影模型**逐顶点**转换坐标/高程；SRSOrigin 不变，轴序自动换算；平面/高程可拆分 |
| **点云模型转换** | LAS/LAZ 逐点转换 X/Y/Z；**只动坐标**，其余属性与头部元数据一字不改；平面/高程可拆分 |
| 多项式转换 | 二维/三维多项式复杂坐标系转换（完全二次、双侧重心化、改正数拟合） |
| 文件转换 | DXF/DWG/SHP/PGDB(MDB)/文本点文件整目录批量转换 |
| 精度对比 | 两个参数喂同一批点对逐点比残差，量化该用哪个 |
| 高斯投影换带 / 空间转换 | UTM 尺度、任意带、抵偿投影面、严密/近似工程椭球；XYZ/BLH/xyH 六型互转 |
| 参数库 / 台账 | 参数 JSON 导入导出、自定义椭球全局可用；操作全程留痕 |

## 快速上手

1. 启动软件（免安装版双击 `GeoRefine.exe`；源码版 `python main.py`）；
2. 大地水准面格网（gtx）放入软件目录的 `models/`（或 `python tools/download_geoid_models.py`）；
3. 控制点转换页导入点对 → 计算与比选 → 保存参数 → 其余页面直接选用。

## 约定

平面坐标 x=北、y=东；高程异常 ξ = 大地高 − 正常高；角度支持 d.ms 编码（105.302568 = 105°30′25.68″）与十进制度。
含带号高斯坐标自动识别分带并锁定中央子午线。
OSGB 顶点为 (东,北,高)、LAS 的 X=东 Y=北，与本软件 (北,东) 相反，**软件自动换算**。
详见 `docs/技术手册.md`。

## 大模型数据转换

两个转换器与单点转换**共用同一套坐标换算链路**（`apply_points`），不存在两套算法。

| | OSGB 倾斜摄影 | LAS/LAZ 点云 |
|---|---|---|
| 实测性能 | 1537 瓦片 / 2098 万顶点 / 16 线程 **约 23 秒** | **每秒上千万点**；4913 万点单文件约 7 秒（含读写） |
| 实现 | C++ 内存态遍历，静态链接 OSG（`app/osgb/cpp/`） | 整块矩阵化（numpy） |
| 完整性 | SRSOrigin 不变 | 非坐标属性与头部元数据逐位保留 |

**OSGB 转换开箱即用**：免安装版内置 Windows 原生转换器（`native/osgb_vertex_transform.exe`，
全静态链接，约 25 MB，**只依赖 Windows 系统 DLL** —— 不需要 WSL、不需要安装 OpenSceneGraph）。
该 exe 由本仓库的 `app/osgb/cpp/build_windows_native.sh` 从 OpenSceneGraph 3.6.5 源码静态构建，
完整过程见 `docs/BUILD_WINDOWS_OSG.md`。若需自行重建：

```bash
# WSL / Linux
sudo apt-get install -y libopenscenegraph-dev
bash app/osgb/cpp/build.sh /tmp/osgb_vertex_transform
```

界面会按 `native/` → `app/osgb/cpp/native/` → WSL `/tmp/` 的顺序自动查找；
都找不到时才提示构建，且不影响其它功能。**LAS 点云转换同样无需任何外部工具。**

## 精度验证

高斯投影/换带与独立参考数据逐位对照（144 组真值，最大偏差 ≤0.5 µm）；正算另与国际开源库 PROJ 对照。
多项式转换按完全二次模型的解析解校验（良态数据 5e-12 一致）。
OSGB/LAS 端到端与 `apply_points` 逐点对照（偏差 ≤1e-9 m，受源数据量化步长限制）。
全部结论见 `docs/技术手册.md` §3。

## 自行修改（源码随包提供）

免安装版 `GeoRefine/` 目录里同时包含完整源代码：

```
GeoRefine/
  GeoRefine.exe        <- 双击运行（无需 Python）
  _internal/           <- Python 运行时与依赖（勿手动改）
  src/                 <- 完整源代码（app/ tests/ tools/ docs/ main.py GeoRefine.spec）
  native/              <- Windows 原生 OSGB 转换器（全静态，无需 WSL/OSG）
  models/              <- 大地水准面格网放这里
  params/              <- 转换参数放这里（JSON，可直接拷给别人）
  logs/                <- 操作台账
  config.json          <- 可选项，如 {"enable_poly3d": true}
```

`src/` 里的代码与 exe 内的逻辑一致，遇到 BUG 可直接把它交给任意 AI 编程工具修改；
改完用 `pip install -r src/requirements.txt` + `python src/main.py` 即可验证，
或按 `src/GeoRefine.spec` 重新打包。

## 已知限制

- DWG 依赖本机 AutoCAD（COM），无 AutoCAD 时该格式降级提示；
- 点云转换目前只支持 LAS/LAZ（用 laspy）；E57/PCD 未实现；
- 大地水准面格网支持 gtx/tif/csv/zgf/ggf/grd/bin（tif 需 imagecodecs）；EGM 数据再分发条款自行确认（提供下载脚本）；
- 其余见技术手册 §4。
