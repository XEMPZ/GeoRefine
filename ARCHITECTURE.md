# GeoRefine — 似大地水准面精化与坐标转换软件 · 架构契约 v1.0

项目根目录：`C:\Users\evilbaby\.openclaw-autoclaw\workspace\geoid_suite\`
技术栈：Python 3.13 + PySide6（GUI）+ numpy（解算）+ Pillow/piexif（照片 EXIF/XMP）+ ezdxf（DXF）。**不依赖 pyproj/scipy**，投影与拟合全部自研。
所有源文件 UTF-8 无 BOM。Windows 控制台跑 Python 前必须设 `$env:PYTHONIOENCODING="utf-8"`。

---

## 0. 坐标与单位约定（全项目强制）

- 平面坐标一律采用**测量约定：x=北坐标(N)，y=东坐标(E，含 500km 假东偏移)**，与控制点 CSV、手簿习惯一致。
- CAD 内部坐标是 (X=东，Y=北)，与测量约定相反。CAD 模块内部做映射：`x_测 = Y_cad`，`y_测 = X_cad`，转换后换回。
- 所有角度接口用**度**（除七参数内部计算用弧度，输出/存储用角秒）。
- 高程：大地高 H（椭球）、正常高 h（水准）。高程异常 ξ = H − h。
- 高程拟合统一语义：拟合对象值 `v`，`value_type='xi'` 时 正常高 = 大地高 − v；`value_type='dh'` 时 目标高 = 源高 + v。

## 1. 目录结构与文件所有权（不许越界写文件）

```
geoid_suite/
  main.py                    # [E] 入口
  requirements.txt           # [主线]
  README.md                  # [主线]
  run_tests.py               # [D] 通用测试 runner：发现 tests/test_*.py 并执行其 run()，打印 PASS/FAIL 汇总，非零退出码=失败
  ARCHITECTURE.md            # [主线] 本文件
  app/
    __init__.py              # [主线]
    common/                  # [主线] params.py / logutil.py / io_csv.py / hconv.py
    core/                    # [A] ellipsoid/projection/transform2d/transform3d/heightfit/geoid/autoselect + __init__
    photo/                   # [B] pos_io/batch + __init__
    cad/                     # [C] dxf_transform + __init__
    gui/                     # [E] 全部界面文件 + styles.py
  models/                    # 大地水准面格网文件（EGM96/EGM2008/用户导入）
  params/                    # 参数库 JSON
  logs/                      # operation_log.jsonl
  sample_data/               # [D] 样例数据
  tests/                     # test_core[A] / test_photo[B] / test_cad[C] / test_pipeline[D2] / test_gui_smoke[E]
  tools/                     # make_sample_data.py[D] / download_geoid_models.py[主线] / build_exe.bat[E或主线]
  docs/                      # [F] 用户手册.md+html / 交接文档.md+html
```

## 2. 核心算法接口（app/core/，仅 numpy）

### ellipsoid.py
```python
@dataclass(frozen=True)
class Ellipsoid: name:str; a:float; inv_f:float
    # e2 = 2f - f^2 提供 property
WGS84 = Ellipsoid("WGS84", 6378137.0, 298.257223563)
CGCS2000 = Ellipsoid("CGCS2000", 6378137.0, 298.257222101)
BEIJING54 = Ellipsoid("Beijing54", 6378245.0, 298.3); XIAN80 = Ellipsoid("Xian80", 6378140.0, 298.257)
ELLIPSOIDS = {"WGS84":WGS84, "CGCS2000":CGCS2000, "Beijing54":BEIJING54, "Xian80":XIAN80}
def geodetic_to_ecef(lat_deg, lon_deg, h, ell=WGS84) -> (x,y,z)   # 标量或 np.ndarray 向量化
def ecef_to_geodetic(x, y, z, ell=WGS84) -> (lat_deg, lon_deg, h) # 迭代法，收敛 1e-12
```

### projection.py（高斯-克吕格，6 阶级数展开）
```python
def gauss_kruger(lat_deg, lon_deg, l0_deg, ell=CGCS2000, x0=0.0, y0=500000.0, k=1.0) -> (x_north, y_east)
def gauss_kruger_inverse(x_north, y_east, l0_deg, ell=CGCS2000, x0=0.0, y0=500000.0, k=1.0) -> (lat_deg, lon_deg)
def auto_central_meridian(lon_deg, band=3) -> float   # 3度带: round(lon/3)*3
```
精度要求：正反算往返 |ΔB|<1e-9°、|ΔL|<1e-9°、平面 <0.1mm；L==L0 时 y_east==500000 精确成立。

### transform2d.py（平面四参数，x=北,y=东）
```python
@dataclass Param4: dx:float; dy:float; a:float; b:float
    # 模型: x2 = dx + a*x1 - b*y1 ; y2 = dy + b*x1 + a*y1
    # property scale = hypot(a,b) ; rot_rad = atan2(b,a) ; rot_sec = rot_rad*206264.8062
def fit_4param(src_xy:(n,2), dst_xy:(n,2)) -> FitResult4(param:Param4, res_xy:(n,2), rms_xy:float, max_xy:float)
def apply_4param(p:Param4, x, y) -> (x2, y2)
def to_survey_xy(X_cad, Y_cad) -> (x_north, y_east)   # = (Y_cad, X_cad)，CAD 模块用
def to_cad_xy(x_north, y_east) -> (X_cad, Y_cad)
```
最少 2 点（恰好 2 点时残差为 0，结果须带 `note='无检核条件'` 提示）。

### transform3d.py（布尔莎七参数，空间直角坐标）
```python
@dataclass Param7: dx,dy,dz:float; rx_s,ry_s,rz_s:float; scale_ppm:float
def fit_7param(src_xyz:(n,3), dst_xyz:(n,3)) -> FitResult7(param, res_xyz:(n,3), rms_3d, max_3d)
    # 线性化布尔莎最小二乘，未知数 [dX,dY,dZ,m,Rx,Ry,Rz]，每点 3 行设计矩阵:
    # X2 = dX + (1+m)X1 - Rz*Y1 + Ry*Z1
    # Y2 = dY + (1+m)Y1 + Rz*X1 - Rx*Z1
    # Z2 = dZ + (1+m)Z1 - Ry*X1 + Rx*Y1
def apply_7param(p:Param7, x, y, z) -> (x2,y2,z2)   # 完整形式(含旋转矩阵)，与拟合自洽
def blh_to_xyz_arr(lat_arr, lon_arr, h_arr, ell) / xyz_to_blh_arr(...)  # 便捷批量
```
最少 3 点；少于 4 点结果须带"无检核条件/弱图形"提示。

### heightfit.py（高程拟合，兼容中海达 GNSSTools 约定）
```python
@dataclass HeightFit:
    mode:str            # 'const' | 'plane' | 'quadratic'
    value_type:str      # 'xi' | 'dh'
    space:str           # 'lonlat' | 'planar'
    coef:np.ndarray     # const:[A]; plane:[A,B,C]; quadratic:[A,B,C,D,E,F]
    center:tuple        # (cx, cy) 中心点
    loo_rms:float|None  # 留一交叉验证 RMS
    note:str
MIN_POINTS = {"const":1, "plane":3, "quadratic":6}
def fit_height(coords:(n,2), values:(n,), mode:str, value_type, space, center_ref="last") -> HeightFit
    # center_ref='last'：中心点=最后一个控制点坐标（GNSSTools 手簿逐点一致约定）；'centroid'=重心
    # 坐标先减中心再入设计矩阵；plane: [1, dx, dy]；quadratic: [1, dx, dy, dx², dy², dx·dy]
    # 正规方程 x=(AᵀA)⁻¹Aᵀb，np.linalg.lstsq；n<MIN_POINTS[mode] 抛 ValueError（不静默降级）
def eval_height(f:HeightFit, x, y) -> float|np.ndarray    # v = A + B·dx + C·dy (+ D·dx²+E·dy²+F·dx·dy)
def select_best_fit(coords, values, value_type, space) -> HeightFit
    # 自动选阶：可行模式全部拟合，按 loo_rms 最小者当选（防止过拟合），拟合过程记录进 note
```

### geoid.py（大地水准面格网）
```python
@dataclass GridData: name; lat_min; lat_max; lon_min; lon_max; dlat; dlon; values:(nlat,nlon); fmt:str; path:str
    # values[i,j] 对应 lat=lat_min+i*dlat, lon=lon_min+j*dlon；缺失/无效格点=999 标记
def load_grid(path) -> GridData      # 按扩展名+魔数自动分发
def read_gtx(path) / read_zgf(path) / read_ggf(path) / read_grd(path) / read_geoid99(path) / read_csv_grid(path) / read_tif_grid(path)
    # ZGF/GGF/Geoid99/GRD 字节布局按《算法规格书01-中海达GNSSTools高程拟合.html》第五节实现（小端序，nodata=999）
    # GTX（PROJ 权威格式，已核实 PROJ 源码 grids.cpp）: 头 40 字节全大端 =
    #   minlat(double,度) minlon(double) dlat(double) dlon(double) rows(int32) cols(int32)
    #   数据 float32 大端行主序；第 0 行=最南端(lat_min)，行内自西向东；节点注册（非像元中心）
    #   nodata=-88.8888 → 读入后统一转为内部无效标记 999
    # CSV 格网：列 lon,lat,undulation（UTF-8/GBK 自动，# 开头注释），先读后判断是否规则格网，不规则按最近邻退化并在 note 注明
    # read_tif_grid：可选依赖 tifffile（ImportError 时返回明确错误信息，不崩）
class GridModel:   # 高程异常查询器
    def __init__(self, grid:GridData, method:str="bilinear")
    def undulation(self, lon_deg, lat_deg) -> float   # 超范围抛 ValueError（message 含范围）
    def try_undulation(self, lon, lat) -> float|None  # 超范围返回 None
def available_models(models_dir) -> list[dict]        # {name, path, fmt, lat_min..., usable}
def make_grid_from_csv_save(...)  # 不需要；导入即复制到 models/
```
合成 GTX 读写往返是正确性证明的硬性测试（见测试规范）。

### autoselect.py（方法自动比选，手簿风格）
```python
def detect_coord_kind(arr2d) -> 'lonlat'|'planar'
    # 经纬度判据: 两列值域大致在 [-90..90]/[-180..180] 且 |值|<1000；否则 planar
def compare_methods(src_pts, dst_pts, src_h, dst_h, src_kind, dst_kind, ell=WGS84, l0=None) -> list[dict]
    # 返回对比表，每行: {"method", "feasible", "n", "rms_xy", "rms_h", "loo_rms_h", "selected", "note"}
    # 方法池:
    #   planar4          平面四参数（src/dst 均为 planar）        最少2点
    #   seven            空间七参数（src/dst 均为 lonlat 或 ecef）最少3点
    #   chain74          七参数+投影+四参数 链式（src lonlat→dst planar）
    #   direct_gk        直接高斯投影（同基准校验用基线）
    # 高程方案独立评估: heightfit mode const/plane/quadratic 各自 loo_rms，选最小
    # selected 规则: 可行方法中 rms_xy 最小；note 给出口径提示（四参数适用小测区≈≤50km²，七参数适用更大范围/跨基准）
def build_param_dict(selection_row, fits..., name, ell, l0) -> dict   # 生成参数库 JSON（见 §3）
```

## 3. 参数库 JSON schema（coordparam/1）

```json
{
  "schema": "coordparam/1",
  "name": "XX测区_四参数+平面拟合",
  "kind": "planar4 | planar3 | seven | three | seven2d | chain74 | chain73 | planar4deg | direct_gk | heightfit | geoid | poly2d | poly3d | inv_direct | inv_chain3 | inv_chain",
  "source_kind": "lonlat|planar", "target_kind": "planar|lonlat",
  "ellipsoid": "WGS84|CGCS2000|Beijing54|Xian80|自定义...",
  "projection": {"l0": 114.0, "x0": 0.0, "y0": 500000.0, "k": 1.0, "h0": 0.0, "rigorous": false, "b0": 30.4},
  "projection_src": {"l0": 114.0, "x0": 0.0, "y0": 500000.0, "h0": 0.0, "rigorous": false, "b0": 30.4, "zone": 38, "band": "3度带"},
  "planar4": {"dx":0.0,"dy":0.0,"a":1.0,"b":0.0},
  "planar3": {"dx":0.0,"dy":0.0},
  "three":   {"dx":0.0,"dy":0.0,"dz":0.0},
  "seven":   {"dx":0.0,"dy":0.0,"dz":0.0,"rx_s":0.0,"ry_s":0.0,"rz_s":0.0,"scale_ppm":0.0},
  "poly":    {"kind":"poly2d|poly3d","degree":2,"ndim":2,"center":[...],"terms":[[..],..],"coefs":[[..],..]},
  "heightfit": {"mode":"plane","space":"lonlat","value_type":"xi","coef":[...],"center":[x,y],"loo_rms":0.008},
  "accuracy": {"rms_xy_m":0.012,"max_xy_m":0.03,"rms_h_m":0.02,"loo_rms_h_m":0.02,"n_points":8,"note":""},
  "created": "2026-08-29T12:00:00",
  "points_used": ["P1","P2"]
}
```

- `projection` 为目标侧投影定义（目标=平面时使用）；`projection_src` 为源侧投影
  （源=工程平面坐标、逆向链 inv_direct/inv_chain3/inv_chain 反算源大地坐标时使用；
  `zone`/`band` 非空表示源 y 含带号且已自动剥离，L0 由带号确定：3度带 24~45→L0=3n，
  6度带 13~23→L0=6n−3）。
- `poly` 为多项式转换模型本体（app.logic.polynomial，页面须保留适用性说明）。

## 4. 公共模块（app/common/，主线实现）

### params.py
```python
class ParamLibrary:
    def __init__(self, dir_path="params")     # 自动建目录
    def save(self, param:dict) -> str         # name 为文件名主体（非法字符替换_），UTF-8 JSON
    def list(self) -> list[dict]              # 摘要: name/kind/created/rms/n
    def load(self, name) -> dict
    def delete(self, name)
    def import_file(self, path) / export_file(self, name, path)
class ParamApplier:      # 按 param dict 应用转换（import app.core）
    def __init__(self, param:dict)
    def apply_lonlat(self, lat, lon, h) -> (x_north, y_east, h_out)   # seven→GK→planar4(可选)→heightfit(可选)
    def apply_planar(self, x_north, y_east, h) -> (x2, y2, h2)
    def eval_height_value(self, coord_pair) -> float|None             # 高程拟合值 v（无则 None）
```

### logutil.py
```python
class OpLog:  # logs/operation_log.jsonl 追加写
    def log(self, op:str, summary:dict)   # {"time": iso, "op": op, ...summary}
    def read_all(self, limit=None) -> list[dict]
    def export_csv(self, path)            # UTF-8-BOM，Excel 可直开
```
op 取值：`param_fit / param_save / param_delete / photo_batch / photo_rollback / cad_transform / accuracy_report`。

### io_csv.py
```python
def read_points_csv(path) -> list[dict]
    # 分隔符自动（逗号/制表/空白）；编码 utf-8-sig/gbk 自动；# 开头为注释行
    # 表头智能识别列: 点名/name/point, x/X/北, y/Y/东, h/H/正常高, B/纬度/lat, L/经度/lon, H_ell/大地高
    # 返回 [{"name":..., } ] 列名统一为: name,x,y,h,B,L,H_ell,H（缺省键值为 None）
def write_csv_utf8bom(path, header:list, rows:list[list])
```

### hconv.py（高程转换统一入口，照片/CAD/精度对比共用）
```python
def make_photo_height_fn(mode:str, model=None, param=None, offset=0.0) -> Callable[[lon,lat,h_ell],(h_new, v, detail)]
    # mode: 'geoid'(GridModel) | 'param'(heightfit, space=lonlat) | 'offset'
    # geoid: h_new = h_ell - xi; 超范围 → (None, None, '超出模型范围')
def make_cad_height_fn(mode:str, param=None, model=None, offset=0.0, apply_2d=False) -> Callable[[X_cad,Y_cad,z],(z_new, v, detail)]
    # mode: 'param'(heightfit, space=planar) | 'offset' | 'none'
```

## 5. 照片 POS 模块（app/photo/，依赖 piexif + Pillow）

### pos_io.py
```python
@dataclass PosRecord: lat:float|None; lon:float|None; h_ell:float|None; source:str; abs_alt:float|None; rel_alt:float|None; raw:dict
def extract_pos(jpg_path) -> PosRecord
    # EXIF GPS: GPSLatitude/LatitudeRef/GPSLongitude/LongitudeRef/GPSAltitude（大地高，米）
    # XMP drone-dji: AbsoluteAltitude / RelativeAltitude / GpsLatitude / GpsLongitude（best-effort，解析失败不报错）
    # EXIF 优先；都无 → lat/lon/h 为 None，source='none'
def set_gps_altitude(jpg_path, new_h:float) -> bool
    # piexif 重写 GPSAltitude（rational，分母 100，毫米精度），经纬度与 Ref 不动；返回是否成功
def set_xmp_absolute_altitude(jpg_path, new_h:float) -> bool
    # best-effort：在 APP1 XMP 段内等字节替换 drone-dji:AbsoluteAltitude 值；失败返回 False 不崩
```

### batch.py
```python
def scan_photos(root:str) -> list[Path]     # rglob，扩展名 jpg/jpeg 大小写不敏感，排序稳定
def process(root:str, height_fn, backup_dir_name="_POS备份", cancel:threading.Event|None=None,
            progress_cb=None) -> dict
    # 逐文件: extract_pos → h_new,height_v,detail = height_fn(lon,lat,h_ell) → 备份 → set_gps_altitude(→set_xmp)
    # 备份: <root>/_POS备份/<相对路径原样镜像>，仅首次写前备份
    # 台账行: {"seq","path","relpath","lat","lon","h_before","v","h_after","mode","status","backup","error","time"}
    # status: ok / no_pos / out_of_range / write_failed / skipped(取消)
    # 返回 {"rows":[...], "ok":n, "failed":n, "skipped":n, "total":n, "ledger_csv":path}
    # progress_cb(done, total, current_path)；每文件检查 cancel；台账 CSV 自动写 <root>/POS处理台账_<ts>.csv (UTF-8-BOM)
def rollback(root:str, backup_dir_name="_POS备份") -> dict
    # 从 _POS备份 按镜像结构还原，返回还原/缺失统计
```

## 6. CAD 模块（app/cad/，依赖 ezdxf）

### dxf_transform.py
```python
@dataclass CadResult: total:int; transformed:int; skipped:int; by_type:dict; unsupported:dict; out_path:str; log:list
def transform_dxf(in_path, out_path, planar:Param4|None, height_fn=None, apply_2d_elevation=False,
                  progress_cb=None, cancel=None) -> CadResult
def detect_dwg(path) -> bool     # 二进制头 'AC10' 魔数检测
```
实现要求：
- 平面变换用 `ezdxf.Matrix44`（实测 ezdxf 1.4 为行向量右乘 v·M，平移在第 4 行）。CAD(X东,Y北) 与测量(x北,y东) 互换后，四参数变换为：
  `X2 = dy + b*Y_cad - a*X_cad`、`Y2 = dx + a*Y_cad - b*X_cad`
  → 矩阵行主序：`行1=[-a,-b,0,0]，行2=[b,a,0,0]，行3=[0,0,1,0]，行4=[dy,dx,0,1]`
- 逐实体 `e.transform(M)`，try/except 收集 unsupported；**不遍历块定义内容**（INSERT 整体 transform，子实体自动跟随，防双重变换）。
- 高程：对带 Z 的点位（LINE 两端、POINT、CIRCLE/ARC 圆心、TEXT/MTEXT 定位点、3DPOLY/POLYLINE 顶点、SPLINE 控制+拟合点、ELLIPSE、3DFACE、SOLID）按 `height_fn(X,Y,z)` 重写 z；LWPOLYLINE 仅当 `apply_2d_elevation=True` 时按首顶点平面位置写 dxf.elevation。
- 输出前统计实体数 before/after 必须一致；图层/线型/属性（XDATA/MTEXT 内容）不动。
- DWG 输入：detect_dwg 命中 → 抛 ValueError("检测到 DWG 二进制格式，请先在 CAD 中另存为 DXF（或用 ODA File Converter 转换）")。

## 7. GUI 规范（app/gui/ + main.py，E 负责）

- 主窗口 `QMainWindow`，左侧图标导航 + `QStackedWidget` 页面。窗口标题「似大地水准面精化与坐标转换软件 GeoRefine v1.0」。启动时 HiDPI 属性 + 1400×860 默认尺寸 + QSettings 记忆。
- 页面：① 首页（功能入口卡片 + 模型/参数库状态摘要）② 控制点转换 ③ 照片POS处理 ④ CAD转换 ⑤ 精度对比 ⑥ 参数库 ⑦ 处理台账 ⑧ 帮助。
- 控制点转换页：点对表格（可编辑）+ 导入CSV/TXT + 源/目标类型自动识别显示 + [自动比选并计算] → 方法对比表（QTableWidget，含 rms/loo/note/selected 高亮）→ 所选参数明细 + [保存到参数库]（命名对话框）。手动输入、单行添加/删除。
- 照片POS页：目录选择 + 递归扫描统计（后台线程） + 高程转换方式（大地水准面模型下拉[来自 models/] / 参数库高程拟合参数 / 常数偏移）+ 是否更新XMP + [开始处理]/[取消] + 进度条 + 结果台账表 + [回滚本次] + 打开台账CSV。
- CAD页：文件（多选）+ 平面参数（参数库选择或无）+ 高程方式（参数/偏移/不转换）+ apply_2d_elevation 勾选 + 输出目录 + [开始转换] + 报告表。
- 精度对比页：加载对比点CSV（点名,lon,lat,H_ell,H_normal）+ 模型多选（EGM96/EGM2008/局部格网…）+ [含拟合]开关（选参数库拟合参数）+ 指标表（RMS/平均/最大）+ 内嵌简易条形图（QPainter 自绘，无 matplotlib 依赖）+ [导出报告]（CSV + 自包含 HTML）。
- 参数库页：列表 + 查看JSON + 删除 + 导入/导出。台账页：OpLog 表格展示 + 导出CSV + 清空。
- **线程铁律**：扫描/批处理/CAD 全部 QThread（或 QThreadPool+QRunnable），信号槽更新 UI，cancel 用 threading.Event；主线程禁止任何>100ms 的同步工作；页面销毁时安全停线程。
- QSS（styles.py，Stamen 地图学风格）：bg #f4f1ea、panel #ffffff、border #d8d2c4、ink #1e2a32、muted #6b7a83、accent #2a6f8e、accent2 #7a8b6f、warn #c96f4a、表格交替行 #faf8f3；字体 "Microsoft YaHei UI" 9pt；按钮主色 accent 圆角 4px；禁用态明确。
- 空状态/错误：所有列表空时给占位提示；异常统一 QMessageBox（含完整错误文本）+ 写 OpLog。

## 8. 测试规范（tests/，每文件提供 `run()->None`，断言失败抛 AssertionError；兼容 pytest）

- test_core.py [A]：① 已知四参数合成→恢复（位置<1mm、旋转<0.5″、尺度<0.5ppm）② 已知七参数合成→恢复（同量级）③ GK 正反算往返（<0.1mm，L==L0 时 y==500000）④ BLH↔ECEF 往返 ⑤ 合成 GTX 写入线性斜坡→GTX 读取+双线性回读一致（<1e-4m）⑥ ZGF 小端合成→读取一致 ⑦ 平面/二次拟合合成参数恢复 + LOO ⑧ autoselect 对比表结构完整。
- test_photo.py [B]：合成 DJI 风格 JPG（EXIF GPS + XMP drone-dji）→ process（offset 模式）→ EXIF 读回=h期望(±0.01)、备份存在且字节可读、台账行数=照片数、无POS照片 status=no_pos、cancel 中途生效。
- test_cad.py [C]：生成 DXF（LINE/LWPOLYLINE/CIRCLE/ARC/POINT/TEXT/MTEXT/3DPOLY/INSERT≥8类）→ 已知四参数+offset 变换 → 重读断言顶点=期望(1e-6)、实体数/图层不变、DWG 检测函数可用。
- test_pipeline.py [D2]：端到端——样例控制点CSV→compare_methods→ParamApplier→样例照片批处理（geoid=样例CSV格网）→校验台账数值；样例DXF→参数库参数→输出 DXF 校验。
- test_gui_smoke.py [E]：QT_QPA_PLATFORM=offscreen 实例化主窗口，遍历创建各页；用样例数据跑一次控制点计算+保存参数库(tmp)；照片页对 sample_data 跑批（offscreen 下后台线程 join 等待完成）；PySide6 缺失时打印 SKIP 不算失败。
- run_tests.py [D]：遍历 tests/test_*.py 调 run()，try/except 汇总 PASS/FAIL/SKIP + 耗时，末尾退出码。

## 9. 样例数据规范（sample_data/，D 负责，由 tools/make_sample_data.py 生成，生成器不 import app.*）

- `控制点样本_平面对.csv`：12 点，列 `点名,x1,y1,h1,x2,y2,h2`；真值四参数 dx=+102.3456, dy=-56.7890, scale=0.9999975, rot=0.25″；高程 dh=平面拟合(中心化) 幅度 ~0.5m；平面噪声 N(0,0.005m)、高程 N(0,0.008m)，固定随机种子 42。范围约 5km×4km（测区中心 北=3354000, 东=500000 附近，L0=114）。
- `控制点样本_空间对.csv`：10 点，列 `点名,B1,L1,H1,B2,L2,H2`；真值七参数 dx=+1.234, dy=-2.345, dz=+3.456(m), rx=0.30″, ry=-0.20″, rz=0.15″, scale=2.5ppm；测区 B∈[29.9,30.1], L∈[113.9,114.1]。
- `样例测区.dxf`：R2010，含 LINE/LWPOLYLINE/CIRCLE/ARC/TEXT/3DPOLY/POINT/MTEXT + 1 个 INSERT 块，图层 ≥4，坐标与平面对样本同测区。
- `样例照片POS/`：8 张 128×96 JPG，EXIF GPS(纬 29.9~30.1 / 经 113.9~114.1 / GPSAltitude=椭球高 35~60m) + XMP drone-dji(AbsoluteAltitude/RelativeAltitude/GpsLatitude/GpsLongitude)，文件名 IMG_001.JPG…（嵌套两层子目录 A/B/ 检验递归）。
- `局部似大地水准面格网_样例.csv`：规则格网 lon 113.90~114.10 步长 0.01 × lat 29.90~30.10，ξ = 12.0 + 0.0008*(lon-114)*1e5*0.01 ... 定义为随经纬线性+缓变曲面（具体公式写进 README 注释），单位 m。
- `精度对比样本.csv`：15 点 `点名,lon,lat,H_ell,H_normal`，H_normal = H_ell − ξ(样例格网) + N(0,0.02) 种子 7。
- `README.md`：每个文件的生成公式、真值参数、预期用法。

## 10. 文档规范（docs/，F 负责）

- `用户手册.md` + 自包含 `用户手册.html`（内嵌 CSS，Stamen 风格：纸色底/墨色字/水蓝 accent，含目录、每页操作步骤、截图位置占位、FAQ：DWG 转存、格网模型下载与导入、备份回滚、参数复用）。
- `交接文档.md`：架构、模块接口、测试、打包（tools/build_exe.bat → PyInstaller --onedir --windowed，含 --add-data models）、扩展点（新格网格式/新拟合模式）、已知限制（XMP best-effort、DWG、EGM 下载需网络）。

## 11. 里程碑轮次

- 轮1（并行）：A 核心 / B 照片 / C CAD / D 样例数据 —— 各自自测通过。
- 轮2（并行）：E GUI+集成 / D2 端到端测试 / F 文档。
- 轮3：R 复核（数值正确性/实体完整性/界面不卡死代码审查/文档核对）+ 主线修复。
- 收尾：主线打包到 DELIVERY/（源码 zip + 成果总览.html），update_goal complete。
