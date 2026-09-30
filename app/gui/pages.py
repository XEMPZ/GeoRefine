"""功能页面：首页/控制点转换/照片POS/文件转换/精度对比/参数库/台账/关于本软件。"""
from __future__ import annotations

import math
import os
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
                               QFrame, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit,
                               QToolButton,
                               QVBoxLayout, QWidget, QDialog, QDialogButtonBox, QFormLayout)

from app.common import hconv
from app.common.io_csv import read_points_csv
from app.common.logutil import OpLog
from app.common.params import ParamApplier, ParamLibrary
from app.logic import cp_methods as _cp
from app.logic import space_points as _spt
from app.logic import apply_params as _apl
from app.logic import file_jobs as _fj
from app.logic import photo_spec as _pspec
from app.logic import accuracy as _acc
from app.gui.styles import ACCENT, ACCENT2, WARN
from app.gui.workers import CadWorker, PhotoBatchWorker, RollbackWorker, ScanWorker
from app.osgb import flash

_ROOT = Path(__file__).resolve().parent.parent.parent
MODELS_DIR = _ROOT / "models"
PARAMS_DIR = _ROOT / "params"


def _load_app_config() -> dict:
    """读取软件目录 config.json（如 {"enable_poly3d": true}）；缺失/损坏 → 空配置。"""
    p = _ROOT / "config.json"
    try:
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
LOGS_DIR = _ROOT / "logs"



def np_column_stack(rows):
    import numpy as np
    return np.asarray(rows, dtype=float)


def _quiet_warn(page, title, text):
    if not getattr(page, "_quiet", False):
        QMessageBox.warning(page, title, text)


def _quiet_info(page, title, text):
    if not getattr(page, "_quiet", False):
        QMessageBox.information(page, title, text)


def _oplog() -> OpLog:
    return OpLog(LOGS_DIR)


def _title(text: str, sub: str = "") -> QWidget:
    w = QWidget()
    w.setStyleSheet("background: #f4f1ea;")   # 显式纸色底：透明区域在部分环境不补绘父底
    v = QVBoxLayout(w)
    v.setContentsMargins(0, 0, 0, 4)
    t = QLabel(text)
    t.setObjectName("pageTitle")
    v.addWidget(t)
    if sub:
        s = QLabel(sub)
        s.setObjectName("muted")
        s.setWordWrap(True)
        v.addWidget(s)
    return w


def _fill_table(table: QTableWidget, header: list, rows: list[list]):
    table.clear()
    table.setColumnCount(len(header))
    table.setHorizontalHeaderLabels(header)
    table.setRowCount(len(rows))
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            item = QTableWidgetItem("" if val is None else str(val))
            if j > 0 and isinstance(val, float):
                item.setText(f"{val:.4f}")
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            table.setItem(i, j, item)
    table.resizeColumnsToContents()


class _TableReceptor(QObject):
    """数据输入表格统一接收器：
    回车增行/下移 · Delete 或右键删除选中行 · Ctrl+V 粘贴（Excel 制表符/文本分隔） ·
    拖入文件调用 page.load_file。
    """

    def __init__(self, table, page=None):
        super().__init__(table)
        self.table = table
        self.page = page
        table.setAcceptDrops(True)
        table.setContextMenuPolicy(Qt.CustomContextMenu)

    def eventFilter(self, obj, ev):  # noqa: N802
        if ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._enter_add()
                return True
            if ev.key() == Qt.Key_Delete:
                self.delete_selected_rows()
                return True
            if ev.key() == Qt.Key_V and (ev.modifiers() & Qt.ControlModifier):
                self.paste_clipboard()
                return True
        elif ev.type() == QEvent.Drop:
            md = ev.mimeData()
            if md.hasUrls():
                for url in md.urls():
                    path = url.toLocalFile()
                    if path:
                        if self.page is not None and hasattr(self.page, "load_file"):
                            self.page.load_file(path)
                        return True
        elif ev.type() == QEvent.ContextMenu:
            self._context_menu(ev.globalPos())
            return True
        return False

    def _enter_add(self):
        t = self.table
        r, c = t.currentRow(), max(0, t.currentColumn())
        if r == t.rowCount() - 1:
            t.insertRow(t.rowCount())
        t.setCurrentCell(min(r + 1, t.rowCount() - 1), c)

    def delete_selected_rows(self):
        t = self.table
        rows = sorted({i.row() for i in t.selectedIndexes()}, reverse=True)
        for r in rows:
            t.removeRow(r)

    def paste_clipboard(self):
        from PySide6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text.strip():
            return
        t = self.table
        r0 = max(0, t.currentRow())
        c0 = max(0, t.currentColumn())
        lines = [ln for ln in text.splitlines() if ln.strip()]
        for k, ln in enumerate(lines):
            cells = ln.split("	")
            if len(cells) == 1 and (" " in ln or "," in ln):
                cells = ln.replace(",", " ").split()
            r = r0 + k
            if r >= t.rowCount():
                t.insertRow(r)
            for j, val in enumerate(cells[:t.columnCount() - c0]):
                t.setItem(r, c0 + j, QTableWidgetItem(val.strip()))
        t.setCurrentCell(min(r0 + len(lines) - 1, t.rowCount() - 1), c0)

    def _context_menu(self, global_pos):
        from PySide6.QtWidgets import QMenu
        t = self.table
        menu = QMenu(t)
        act_del = menu.addAction("删除选中行")
        act_paste = menu.addAction("从剪贴板粘贴")
        act_clear = menu.addAction("清空表格")
        act = menu.exec(global_pos)
        if act is act_del:
            self.delete_selected_rows()
        elif act is act_paste:
            self.paste_clipboard()
        elif act is act_clear:
            t.setRowCount(0)
            t.insertRow(0)


def _enable_enter_add(table, page=None):
    """开启表格交互：回车增行、即点即输、Delete/右键删行、粘贴、拖入文件。

    返回 receptor 引用（需持有防回收）。page 需有 load_file(path) 以支持拖入。
    """
    f = _TableReceptor(table, page)
    table.installEventFilter(f)
    table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed |
                          QAbstractItemView.SelectedClicked | QAbstractItemView.AnyKeyPressed)
    return f


def _sync_ell_fields(page):
    """椭球下拉联动 a/1/f 输入框：预设只读回填，"自定义"开放编辑。"""
    from app.core.ellipsoid import get_ellipsoid
    name = page.ell_combo.currentText()
    custom = name == "自定义"
    if not custom:
        ell = get_ellipsoid(name)
        page.a_edit.setText(f"{ell.a:.4f}")
        page.invf_edit.setText(f"{ell.inv_f:.9f}")
    for w_ in (page.a_edit, page.invf_edit):
        w_.setReadOnly(not custom)


def _current_ell_from(page):
    """读取页面当前椭球（预设或自定义，含合理性校验）。"""
    from app.core.ellipsoid import Ellipsoid, get_ellipsoid
    name = page.ell_combo.currentText()
    if name != "自定义":
        return get_ellipsoid(name)
    try:
        a = float(page.a_edit.text())
        invf = float(page.invf_edit.text())
    except ValueError as e:
        raise ValueError(f"自定义椭球参数需为数值：{e}") from e
    if not (1e6 < a < 1e7 and 100 < invf < 500):
        raise ValueError(f"自定义椭球参数超出合理范围：a={a}, 1/f={invf}")
    return Ellipsoid("自定义", a, invf)


# ============================================================ 首页
class HomePage(QWidget):
    """首页：软件定位 → 核心优点 → 特色功能（无人机 POS 免相控）→ 功能导览。"""

    _TOUR = [
        ("控制点转换", "把 GNSS 大地坐标与施工坐标的公共点对导入，软件自动把所有可行方案算一遍并按精度排序，挑出最优的转换参数存进参数库。", 4),
        ("参数应用转换", "拿参数库里保存好的参数，批量转换一份坐标清单（粘贴或导入表格即可）。", 3),
        ("照片 POS 处理", "特色功能：把无人机照片里的 POS 大地高直接换成正常高并写回照片，实现免相控测量。", 5),
        ("OSGB 模型转换", "对倾斜摄影 OSGB 模型的每个顶点做坐标/高程转换；SRSOrigin 保持不变，模型可直接在原有引擎里加载。", 6),
        ("点云模型转换", "LAS/LAZ 点云逐点转换 X/Y/Z；<b>只动坐标，强度/回波/分类/GPS 时间/RGB 等一律原样保留</b>。", 7),
        ("多项式转换", "复杂坐标系转换的补充手段（二维/三维多项式）；算法口径与主流商用内核对齐。", 8),
        ("文件转换", "把整个文件夹的 CAD（DXF/DWG）、SHP、文本点文件按参数批量转换坐标与高程。", 9),
        ("精度对比", "同一批点对喂给两个不同参数（例如不同大地水准面模型），逐点比残差，告诉你该用哪个。", 10),
        ("高斯投影换带", "单点的高斯投影/换带计算：支持 UTM 尺度、加常数、抵偿投影面与严密/近似工程椭球。", 1),
        ("空间坐标转换", "空间直角（XYZ）、大地（BLH）、平面+高程（xyH）六种形式两两互转。", 2),
        ("参数库", "管理算好的转换参数与椭球：导入导出、删除、查看明细；文件都放在 params/ 目录，可直接拷贝给别人。", 11),
        ("处理台账", "每次计算、回写、转换都留痕，可导出 CSV、可清空。", 12),
    ]

    # ★ 特色功能（首页卡片）：标题、说明、导航页号
    _FEATURES = [
        ("无人机照片 POS 处理",
         "免相控测量的关键一步。直接读取照片 EXIF/XMP 里的 POS 椭球高，批量换算成施工坐标系的正常高后"
         "<b>原位写回照片</b>——主流建图软件开启“POS 高程模式”即可直接出正常高成果，外业不再打像控点。"
         "支持多目录递归扫描、dry-run 预览、自动备份（_POS备份/，永不二次覆盖）、逐张台账与一键回滚。",
         5),
        ("OSGB 模型转换",
         "把坐标转换直接作用到倾斜摄影模型的<b>每个顶点</b>上，转完还能在原有引擎里正常加载。"
         "平面与高程可独立勾选；支持平面源参数（四参数/三参数/二维七参数/平面多项式）与"
         "大地源参数（七参数链/直接投影）。高程含大地水准面格网时按源侧经纬度插值 ξ；"
         "OSGB 的 (东,北) 轴序自动换算。",
         6),
        ("点云模型转换",
         "LAS/LAZ 点云逐点转换 X/Y/Z，<b>只动坐标</b>——强度、回波、分类、GPS 时间、RGB、扫描角、"
         "点源 ID 与自定义维度一律原样保留，点格式与 scale/offset 也不改。"
         "平面与高程同样可独立勾选；带号自动识别，无 CRS 的数据可手填中央子午线。",
         7),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(24, 18, 24, 18)
        v.setSpacing(10)

        head = QLabel(
            "<b style='font-size:15pt;color:#2a6f8e'>GeoRefine — 似大地水准面精化与坐标转换软件</b>"
            "<span style='color:#6b7a83'>　v1.1</span>")
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)
        intro = QLabel(
            "<b>功能说明</b><br>"
            "测量外业拿到的是 GNSS 大地坐标（B, L, 大地高 H），而设计和施工用的是平面坐标（x, y）和水准正常高 h——"
            "两者差着一个“高程异常 ξ”，还常常差着一套坐标系。本软件把这条链路完整打通：<br>"
            "① <b>解算</b>：导入公共点对，自动把所有可行方案算一遍并按残差排序，挑出最优参数存入参数库；<br>"
            "② <b>应用</b>：用同一套参数批量转换坐标表、CAD 图（DXF/DWG）、SHP/文本点文件；<br>"
            "③ <b>落到数据上</b>：<b>回写无人机照片 POS 高程</b>（免相控）、"
            "<b>转换 OSGB 倾斜摄影模型顶点</b>、<b>转换 LAS/LAZ 点云坐标</b>；<br>"
            "④ <b>验证</b>：精度对比页把不同参数/大地水准面模型喂同一批点，用数据告诉你该用哪个。<br>"
            "坐标约定：平面 x=北、y=东；高程异常 ξ = 大地高 − 正常高；"
            "OSGB 与 LAS 的 (东,北) 轴序自动换算。")
        intro.setWordWrap(True)
        v.addWidget(intro)

        adv = QGroupBox("为什么用它")
        av = QVBoxLayout(adv)
        for t in [
            "· <b>会自己选方法</b>：导入点对后把四参数、三参数、七参数、七参数+投影+四参数、高程拟合、大地水准面格网全部算一遍，"
            "按残差 RMS 自动排序择优，每个方案都给出精度指标（RMS 与留一验证 LOO）。",
            "· <b>高程两条腿随便比</b>：EGM96/EGM2008 等大地水准面格网与曲面拟合可以单独用、也可以串联（格网+残差趋势项），"
            "“精度对比”页把不同模型/参数喂同一批点，直接告诉你该用哪个。",
            "· <b>口径经过严格验证</b>：高斯投影与换带和权威行业实现的 144 组真值逐位一致（偏差 ≤0.5 µm）；"
            "多项式转换与主流商用内核的公开行为口径对齐；d.ms 度分秒编码与国产手簿一致。",
            "· <b>全程留痕、可回滚</b>：参数存参数库（JSON，可直接拷走）、操作有台账、照片回写前自动备份原始 POS。",
            "· <b>大模型数据也能直接改</b>：倾斜摄影 OSGB 模型与 LAS/LAZ 点云按顶点批量转换坐标高程——"
            "OSGB 用内存态遍历（全模型 1500 余瓦片约 23 秒），点云整块矩阵化（每秒上千万点）；"
            "点云转换只动 X/Y/Z，其余属性与头部元数据一字不改。",
        ]:
            lab = QLabel(t)
            lab.setWordWrap(True)
            av.addWidget(lab)
        v.addWidget(adv)

        feat = QGroupBox("★ 特色功能")
        fv = QVBoxLayout(feat)
        fgrid = QGridLayout()
        fgrid.setSpacing(10)
        for col, (title, desc, nav) in enumerate(self._FEATURES):
            card = QGroupBox(title)
            cv = QVBoxLayout(card)
            lab = QLabel(desc)
            lab.setWordWrap(True)
            cv.addWidget(lab)
            btn = QPushButton("进入 →")
            btn.setProperty("class", "secondary")
            btn.setFixedWidth(90)
            btn.clicked.connect(lambda _=False, k=nav: self._goto(k))
            cr = QHBoxLayout()
            cr.addStretch(1)
            cr.addWidget(btn)
            cv.addLayout(cr)
            fgrid.addWidget(card, 0, col)
            fgrid.setColumnStretch(col, 1)
        fv.addLayout(fgrid)
        v.addWidget(feat)

        tour = QGroupBox("功能导览（点击进入）")
        tg = QGridLayout(tour)
        tg.setSpacing(8)
        for i, (name, desc, idx) in enumerate(self._TOUR):
            card = QGroupBox(name)
            cv = QVBoxLayout(card)
            cl = QLabel(desc)
            cl.setWordWrap(True)
            cl.setObjectName("muted")
            cv.addWidget(cl)
            btn = QPushButton("打开 →")
            btn.setProperty("class", "secondary")
            btn.setFixedWidth(90)
            btn.clicked.connect(lambda _=False, k=idx: self._goto(k))
            cr = QHBoxLayout()
            cr.addStretch(1)
            cr.addWidget(btn)
            cv.addLayout(cr)
            tg.addWidget(card, i // 2, i % 2)
        tg.setColumnStretch(0, 1)
        tg.setColumnStretch(1, 1)
        inner = QWidget()
        inner.setLayout(tg)
        inner.setMaximumWidth(1040)
        from PySide6.QtWidgets import QScrollArea as _QSA
        _sc = _QSA()
        _sc.setWidgetResizable(True)
        _sc.setFrameShape(QFrame.NoFrame)
        _sc.setWidget(inner)
        v.addWidget(_sc, 1)
        self.status_label = QLabel()
        self.status_label.setObjectName("muted")
        self.status_label.setAlignment(Qt.AlignHCenter)
        self.status_label.setWordWrap(True)
        v.addWidget(self.status_label)
        self.refresh_status()

    def _goto(self, nav_index: int):
        """跳转到指定导航页（经主窗口导航列表，保证页面状态联动）。"""
        win = self.window()
        nav = getattr(win, "nav", None)
        if nav is not None:
            nav.setCurrentRow(nav_index)

    def refresh_status(self):
        from app.core.geoid import available_models
        models = available_models(MODELS_DIR)
        # 测试用格网（文件名以 _ 开头）不计入正式模型数，避免误导用户
        usable = [m for m in models if m["usable"] and not m["name"].startswith("_")]
        lib = ParamLibrary(PARAMS_DIR).list()
        self.status_label.setText(
            f"格网模型：{len(usable)} 个可用（{', '.join(m['name'] for m in usable[:3])}…）    "
            f"参数库：{len(lib)} 组参数    台账：logs/operation_log.jsonl")


# ============================================================ 控制点转换
class ControlPointPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("控制点转换", "输入或导入控制点对 → 自动比选转换方法 → 查看参数与残差 → 保存到参数库。"
                                            "支持平面坐标对（x北,y东,h）与大地坐标对（B,L,H），自动识别。"))
        # 输入区
        g1 = QGroupBox("控制点对（可手动编辑；导入支持 手簿.cot / 南方CASS点对 / 南方坐标转换软件点对 / CSV·TXT·XLSX）")
        gv = QVBoxLayout(g1)
        btns = QHBoxLayout()
        self.btn_import = QPushButton("导入点对文件")
        self.btn_export = QPushButton("导出点对文件")
        self.btn_add = QPushButton("追加一行")
        self.btn_del = QPushButton("删除选中行")
        self.btn_clear = QPushButton("清空")
        for b in (self.btn_import, self.btn_export, self.btn_add, self.btn_del, self.btn_clear):
            b.setProperty("class", "secondary")
            btns.addWidget(b)
        btns.addStretch(1)
        hints = QHBoxLayout()
        h1 = QLabel("自动识别：GNSS手簿点校正点对文件 .cot")
        h1.setObjectName("muted")
        hints.addWidget(h1)
        hints.addStretch(1)
        gv.addLayout(hints)
        hints2 = QHBoxLayout()
        h2 = QLabel("自动识别：南方CASS点对 .txt（旧x,旧y,旧h:新x,新y,新h） / 南方坐标转换软件点对 .txt（点名,旧x,旧y,旧h,新x,新y,新h）")
        h2.setObjectName("muted")
        hints2.addWidget(h2)
        hints2.addStretch(1)
        gv.addLayout(hints2)
        gv.addLayout(btns)
        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels(["点名", "源x/L", "源y/B", "源h/H",
                                              "目标x", "目标y", "目标h", "用平面", "用高程",
                                              "x残差(m)", "y残差(m)", "h残差(m)"])
        self.table.horizontalHeader().setMinimumSectionSize(56)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(11, QHeaderView.Stretch)
        for c, w in ((0, 90), (1, 96), (2, 96), (3, 78), (4, 96), (5, 96), (6, 78),
                     (7, 62), (8, 62), (9, 66), (10, 66)):
            self.table.setColumnWidth(c, w)
        for c, tip in ((1, "源平面 x(北) 或 经度 L"), (2, "源平面 y(东) 或 纬度 B"),
                       (3, "源正常高 h 或 大地高 H"), (7, "Y/N 或勾选：是否参与平面参数解算"),
                       (8, "Y/N 或勾选：是否参与高程拟合"), (9, "所选方法平面残差 dx"),
                       (10, "所选方法平面残差 dy"), (11, "所选方法高程残差 dh")):
            self.table.horizontalHeaderItem(c).setToolTip(tip)
        self.table.setAlternatingRowColors(True)
        gv.addWidget(self.table)
        v.addWidget(g1, 3)
        # 计算区（四列手簿式：投影参数 / 平面转换 / 高程转换 / 大地水准面）
        g2 = QGroupBox("比选与计算（默认自动判断选最优；也可手动指定各环节）")
        gv2 = QVBoxLayout(g2)
        row = QHBoxLayout()
        self.btn_compute = QPushButton("计算与比选")
        self.lbl_kind = QLabel("坐标类型：待计算")
        self.lbl_kind.setObjectName("muted")
        row.addWidget(self.btn_compute)
        row.addWidget(self.lbl_kind)
        row.addStretch(1)
        gv2.addLayout(row)
        opts = QGridLayout()
        # ① 椭球与投影参数（源/目标侧分区；与高斯投影换带页同口径）
        #    源侧投影：源=工程平面坐标时用于反算大地坐标（逆向链），源=大地/空间直角时灰显；
        #    目标侧投影：目标=平面坐标时的投影定义，目标=大地/空间直角时灰显；
        #    源/目标 y 含带号时自动识别分带与带号并锁定该侧中央子午线。
        proj_g = QGroupBox("① 椭球与投影参数")
        pg = QGridLayout(proj_g)
        from app.core.ellipsoid import all_ellipsoids as _ALL
        self.ell_src_combo = QComboBox()
        for nm in _ALL():
            self.ell_src_combo.addItem(nm)
        self.ell_src_combo.setCurrentText("CGCS2000")
        self.ell_src_combo.setMaximumWidth(170)
        self.same_ell = QCheckBox("同一椭球")
        self.same_ell.setChecked(True)
        self.ell_dst_combo = QComboBox()
        for nm in _ALL():
            self.ell_dst_combo.addItem(nm)
        self.ell_dst_combo.setEnabled(False)
        self.ell_dst_combo.setMaximumWidth(170)
        self.chk_use_proj = QCheckBox("使用投影参数（先投影再四参数——推荐）")
        self.chk_use_proj.setToolTip("源大地坐标先投影再拟合四参数——推荐；"
                                     "不勾选则源经纬度按平面坐标直接拟合四参数")
        self.chk_use_proj.setChecked(True)
        pg.addWidget(self.chk_use_proj, 0, 0, 1, 2)
        pg.addWidget(QLabel("原始椭球"), 1, 0)
        pg.addWidget(self.ell_src_combo, 1, 1)
        pg.addWidget(self.same_ell, 2, 0, 1, 2)
        pg.addWidget(QLabel("目标椭球"), 3, 0)
        pg.addWidget(self.ell_dst_combo, 3, 1)
        sep_s = QLabel("── 源侧投影（源=工程平面坐标时填写，用于反算大地坐标）──")
        sep_s.setObjectName("muted")
        pg.addWidget(sep_s, 4, 0, 1, 2)
        self.proj_l0_s = QLineEdit("105.00000000")
        self.proj_y0_s = QLineEdit("500.00000000")
        self.proj_x0_s = QLineEdit("0.00000000")
        self.h0_s_edit = QLineEdit("0.00000000")
        for _w in (self.proj_l0_s, self.proj_y0_s, self.proj_x0_s, self.h0_s_edit):
            _w.setMaximumWidth(170)
        pg.addWidget(QLabel("中央子午线(d.ms)"), 5, 0)
        pg.addWidget(self.proj_l0_s, 5, 1)
        pg.addWidget(QLabel("y0加常数(km)"), 6, 0)
        pg.addWidget(self.proj_y0_s, 6, 1)
        pg.addWidget(QLabel("x0(km)"), 7, 0)
        pg.addWidget(self.proj_x0_s, 7, 1)
        pg.addWidget(QLabel("投影面大地高(m)"), 8, 0)
        pg.addWidget(self.h0_s_edit, 8, 1)
        self.rb_rig_s = QRadioButton("严密工程椭球")
        self.rb_app_s = QRadioButton("近似工程椭球")
        self.rb_app_s.setChecked(True)
        self.rb_rig_s.setToolTip("严密：a'=a·(1+h0/Ra(B0))；近似：a'=a+h0（行业惯例）")
        _host_s = QWidget()          # 独立容器=独立互斥域（防与目标侧串扰）
        _hs = QHBoxLayout(_host_s)
        _hs.setContentsMargins(0, 0, 0, 0)
        _hs.addWidget(self.rb_rig_s)
        _hs.addWidget(self.rb_app_s)
        _hs.addStretch(1)
        pg.addWidget(_host_s, 9, 0, 1, 2)
        self.b0_s_edit = QLineEdit("")
        self.b0_s_edit.setMaximumWidth(170)
        self.b0_s_edit.setPlaceholderText("留空=自动取参与点平均纬度")
        self.b0_s_edit.setToolTip("严密工程椭球的平均纬度 B0（Ra(B0) 用）；仅严密模式参与计算，留空自动")
        pg.addWidget(QLabel("平均纬度 B0(d.ms)"), 10, 0)
        pg.addWidget(self.b0_s_edit, 10, 1)
        self.lbl_zone_s = QLabel("")
        self.lbl_zone_s.setObjectName("muted")
        self.lbl_zone_s.setWordWrap(True)
        pg.addWidget(self.lbl_zone_s, 11, 0, 1, 2)
        sep_t = QLabel("── 目标侧投影（目标=平面坐标时的投影定义）──")
        sep_t.setObjectName("muted")
        pg.addWidget(sep_t, 12, 0, 1, 2)
        self.proj_l0 = QLineEdit("105.00000000")
        self.proj_y0 = QLineEdit("500.00000000")
        self.proj_x0 = QLineEdit("0.00000000")
        for _w in (self.proj_l0, self.proj_y0, self.proj_x0):
            _w.setMaximumWidth(170)
        pg.addWidget(QLabel("中央子午线(d.ms)"), 13, 0)
        pg.addWidget(self.proj_l0, 13, 1)
        pg.addWidget(QLabel("y0加常数(km)"), 14, 0)
        pg.addWidget(self.proj_y0, 14, 1)
        pg.addWidget(QLabel("x0(km)"), 15, 0)
        pg.addWidget(self.proj_x0, 15, 1)
        self.h0_edit = QLineEdit("0.00000000")
        pg.addWidget(QLabel("投影面大地高(m)"), 16, 0)
        pg.addWidget(self.h0_edit, 16, 1)
        self.rb_rig_cp = QRadioButton("严密工程椭球")
        self.rb_app_cp = QRadioButton("近似工程椭球")
        self.rb_app_cp.setChecked(True)
        self.rb_rig_cp.setToolTip("严密：a'=a·(1+h0/Ra(B0))，Ra 为平均纬度处平均曲率半径；"
                                  "近似：a'=a+h0（行业惯例）")
        _host_t = QWidget()          # 独立容器=独立互斥域
        _ht = QHBoxLayout(_host_t)
        _ht.setContentsMargins(0, 0, 0, 0)
        _ht.addWidget(self.rb_rig_cp)
        _ht.addWidget(self.rb_app_cp)
        _ht.addStretch(1)
        pg.addWidget(_host_t, 17, 0, 1, 2)
        self.b0_t_edit = QLineEdit("")
        self.b0_t_edit.setMaximumWidth(170)
        self.b0_t_edit.setPlaceholderText("留空=自动取参与点平均纬度")
        self.b0_t_edit.setToolTip("严密工程椭球的平均纬度 B0（Ra(B0) 用）；仅严密模式参与计算，留空自动")
        pg.addWidget(QLabel("平均纬度 B0(d.ms)"), 18, 0)
        pg.addWidget(self.b0_t_edit, 18, 1)
        self.lbl_zone_t = QLabel("")
        self.lbl_zone_t.setObjectName("muted")
        self.lbl_zone_t.setWordWrap(True)
        pg.addWidget(self.lbl_zone_t, 19, 0, 1, 2)
        opts.addWidget(proj_g, 0, 0)
        # ② 平面转换（手簿式：独立勾选 + 参数明细展开 + 计算）
        plane_g = QGroupBox("② 平面转换")
        pv = QVBoxLayout(plane_g)
        self.chk_precise_xy = QCheckBox("同椭球高精度二维坐标")
        self.chk_precise_xy.setToolTip("GNSS 静态，转换前后同一椭球参数（图纸/成果只给二维坐标）。"
                                       "坐标系实质差别很小，应采用三参数法（仅平移，无尺度/旋转），"
                                       "避免把高精度噪声吸进尺度与旋转角")
        pv.addWidget(self.chk_precise_xy)
        self.plane_34_w = QWidget()          # 三/四参数互斥域
        pg34 = QGridLayout(self.plane_34_w)
        pg34.setContentsMargins(0, 0, 0, 0)
        # 三/四参数二选一：用 QCheckBox+手动互斥（QRadioButton autoExclusive 一旦勾选
        # 无法程序化取消，导致“全不勾平面”不可能，纯高程/格网参数无法创建）
        self.rb_planar4 = QCheckBox("四参数")
        self.rb_planar3 = QCheckBox("三参数（无尺度/旋转，仅平移）")
        self.rb_planar4.setChecked(True)
        self.btn_cf_4 = QPushButton("计算")
        self.btn_cf_3 = QPushButton("计算")
        for b in (self.btn_cf_4, self.btn_cf_3):
            b.setProperty("class", "secondary")
        self.tb_p_4 = QToolButton(); self.tb_p_4.setText("参数 ▸"); self.tb_p_4.setCheckable(True)
        self.tb_p_3 = QToolButton(); self.tb_p_3.setText("参数 ▸"); self.tb_p_3.setCheckable(True)
        self.lbl_p_4 = QLabel("未计算"); self.lbl_p_3 = QLabel("未计算")
        for w in (self.lbl_p_4, self.lbl_p_3):
            w.setObjectName("muted"); w.setWordWrap(True); w.setVisible(False)
            w.setStyleSheet("margin-left: 18px;")
        pg34.addWidget(self.rb_planar4, 0, 0)
        pg34.addWidget(self.btn_cf_4, 0, 1)
        pg34.addWidget(self.tb_p_4, 0, 2)
        pg34.addWidget(self.lbl_p_4, 1, 0, 1, 3)
        pg34.addWidget(self.rb_planar3, 2, 0)
        pg34.addWidget(self.btn_cf_3, 2, 1)
        pg34.addWidget(self.tb_p_3, 2, 2)
        pg34.addWidget(self.lbl_p_3, 3, 0, 1, 3)
        pg34.setColumnStretch(0, 1)
        pv.addWidget(self.plane_34_w)
        self.chk_seven = QCheckBox("七参数（二维/链式自动适配）")
        self.chk_seven.setToolTip("含二维七参数/链式，按数据类型自动适配")
        sr = QHBoxLayout()
        self.btn_cf_7 = QPushButton("计算")
        self.btn_cf_7.setProperty("class", "secondary")
        self.tb_p_7 = QToolButton(); self.tb_p_7.setText("参数 ▸"); self.tb_p_7.setCheckable(True)
        self.lbl_p_7 = QLabel("未计算")
        self.lbl_p_7.setObjectName("muted"); self.lbl_p_7.setWordWrap(True); self.lbl_p_7.setVisible(False)
        self.lbl_p_7.setStyleSheet("margin-left: 18px;")
        sr.addWidget(self.chk_seven, 1)
        sr.addWidget(self.btn_cf_7)
        sr.addWidget(self.tb_p_7)
        pv.addLayout(sr)
        pv.addWidget(self.lbl_p_7)
        plane_tip = QLabel("勾选＝参与计算与比选；源为大地坐标时七参数走链式（+投影+四参数）；"
                           "“计算”按当前勾选解算")
        plane_tip.setObjectName("muted")
        plane_tip.setWordWrap(True)
        pv.addWidget(plane_tip)
        opts.addWidget(plane_g, 0, 1)
        # ③ 高程转换（手簿式：平面/曲面/大地水准面模型 解耦勾选）
        hgt_g = QGroupBox("③ 高程转换")
        hv = QVBoxLayout(hgt_g)
        self.chk_precise_h = QCheckBox("同椭球高精度大地高与正常高")
        self.chk_precise_h.setToolTip("转换前后分别有高精度 H 与 h。高程拟合方法无法自动判定："
                                      "由用户在下方手动勾选平面拟合/曲面拟合/大地水准面模型")
        hv.addWidget(self.chk_precise_h)
        self.chk_fit_plane = QCheckBox("平面拟合（斜面）")
        self.chk_fit_quad = QCheckBox("曲面拟合（二次曲面）")
        self.chk_geoid_model = QCheckBox("大地水准面模型")
        hr1 = QHBoxLayout()
        self.btn_cf_plane = QPushButton("计算")
        self.btn_cf_quad = QPushButton("计算")
        for b in (self.btn_cf_plane, self.btn_cf_quad):
            b.setProperty("class", "secondary")
        self.tb_p_plane = QToolButton(); self.tb_p_plane.setText("参数 ▸"); self.tb_p_plane.setCheckable(True)
        self.tb_p_quad = QToolButton(); self.tb_p_quad.setText("参数 ▸"); self.tb_p_quad.setCheckable(True)
        self.lbl_p_plane = QLabel("未计算"); self.lbl_p_quad = QLabel("未计算")
        for w in (self.lbl_p_plane, self.lbl_p_quad):
            w.setObjectName("muted"); w.setWordWrap(True); w.setVisible(False)
            w.setStyleSheet("margin-left: 18px;")
        hr1.addWidget(self.chk_fit_plane, 1)
        hr1.addWidget(self.btn_cf_plane)
        hr1.addWidget(self.tb_p_plane)
        hv.addLayout(hr1)
        hv.addWidget(self.lbl_p_plane)
        hr2 = QHBoxLayout()
        hr2.addWidget(self.chk_fit_quad, 1)
        hr2.addWidget(self.btn_cf_quad)
        hr2.addWidget(self.tb_p_quad)
        hv.addLayout(hr2)
        hv.addWidget(self.lbl_p_quad)
        hr3 = QHBoxLayout()
        hr3.addWidget(self.chk_geoid_model)
        self.geoid_combo = QComboBox()
        self.geoid_combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.btn_geoid_dir = QPushButton("打开模型文件夹")
        self.btn_geoid_dir.setProperty("class", "secondary")
        self.btn_cf_geoid = QPushButton("计算")
        self.btn_cf_geoid.setProperty("class", "secondary")
        self.tb_p_geoid = QToolButton(); self.tb_p_geoid.setText("算法 ▸"); self.tb_p_geoid.setCheckable(True)
        hr3.addWidget(self.geoid_combo, 1)
        hr3.addWidget(self.btn_geoid_dir)
        hr3.addWidget(self.btn_cf_geoid)
        hr3.addWidget(self.tb_p_geoid)
        hv.addLayout(hr3)
        self.lbl_p_geoid = QLabel("未计算")
        self.lbl_p_geoid.setObjectName("muted"); self.lbl_p_geoid.setWordWrap(True); self.lbl_p_geoid.setVisible(False)
        self.lbl_p_geoid.setStyleSheet("margin-left: 18px;")
        hv.addWidget(self.lbl_p_geoid)
        hgt_tip = QLabel("平面拟合与曲面拟合互为包含关系（曲面⊃平面），建议二选一；"
                         "勾选大地水准面模型时可与拟合串联（ξ＝格网＋残差趋势项）")
        hgt_tip.setObjectName("muted")
        hgt_tip.setWordWrap(True)
        hv.addWidget(hgt_tip)
        opts.addWidget(hgt_g, 1, 0, 1, 2)
        opts.setColumnStretch(0, 1)
        opts.setColumnStretch(1, 1)
        gv2.addLayout(opts)
        self.cmp_table = QTableWidget(0, 8)
        self.cmp_table.setHorizontalHeaderLabels(["方法", "可行", "点数", "RMS平面(m)", "RMS高程(m)",
                                                  "LOO高程(m)", "选用", "说明"])
        self.cmp_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cmp_table.setAlternatingRowColors(True)
        gv2.addWidget(self.cmp_table, 2)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(120)
        self.detail.setPlaceholderText("所选方法的参数明细将显示在这里")
        gv2.addWidget(self.detail)
        save_row = QHBoxLayout()
        self.btn_save = QPushButton("保存所选方法到参数库")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("参数名称，如：XX测区_四参数+平面拟合")
        self.chk_geoid = QCheckBox("高程基于大地水准面")
        self.chk_geoid.setToolTip("勾选后参数记录『高程以大地水准面为基准』；照片POS选用该参数时将默认勾选 XMP 更新")
        save_row.addWidget(self.chk_geoid)
        save_row.addWidget(self.name_edit, 2)
        save_row.addWidget(self.btn_save)
        gv2.addLayout(save_row)
        v.addWidget(g2, 4)
        # 信号
        self.btn_import.clicked.connect(self.import_csv)
        self.btn_export.clicked.connect(self.export_pairs)
        self.btn_add.clicked.connect(self.add_row)
        self.btn_del.clicked.connect(self.del_row)
        self.btn_clear.clicked.connect(self.clear_rows)
        self.btn_compute.clicked.connect(lambda: self.compute())
        self.btn_save.clicked.connect(self.save_param)
        # 勾选/单选变化 → 按当前勾选重算（不自动改勾选）
        for w in (self.chk_precise_xy, self.chk_precise_h, self.chk_fit_plane,
                  self.chk_fit_quad, self.chk_geoid_model, self.chk_seven):
            w.toggled.connect(self._on_check_changed)
        for rb in (self.rb_planar4, self.rb_planar3):
            rb.toggled.connect(self._on_check_changed)
        self._mutexing = False
        self.rb_planar4.toggled.connect(self._mutex_34)
        self.rb_planar3.toggled.connect(self._mutex_34)
        self.tb_p_4.toggled.connect(self.lbl_p_4.setVisible)
        self.tb_p_3.toggled.connect(self.lbl_p_3.setVisible)
        self.tb_p_7.toggled.connect(self.lbl_p_7.setVisible)
        self.tb_p_plane.toggled.connect(self.lbl_p_plane.setVisible)
        self.tb_p_quad.toggled.connect(self.lbl_p_quad.setVisible)
        self.tb_p_geoid.toggled.connect(self.lbl_p_geoid.setVisible)
        self.btn_cf_4.clicked.connect(self._compute_manual)
        self.btn_cf_3.clicked.connect(self._compute_manual)
        self.btn_cf_7.clicked.connect(self._compute_manual)
        self.btn_cf_plane.clicked.connect(self._compute_manual)
        self.btn_cf_quad.clicked.connect(self._compute_manual)
        self.btn_cf_geoid.clicked.connect(self._compute_manual)
        self.btn_geoid_dir.clicked.connect(self.open_geoid_dir)
        self.geoid_combo.currentIndexChanged.connect(self._on_check_changed)
        self.chk_use_proj.toggled.connect(self._sync_proj_enabled)
        # 大地水准面默认勾选：用户手动点击后不再自动改；椭球切换联动重估
        self.chk_geoid_model.clicked.connect(lambda *_: setattr(self, "_geoid_touched", True))
        self.ell_src_combo.currentIndexChanged.connect(
            lambda *_: self._sync_geoid_default() if hasattr(self, "chk_geoid_model") else None)
        self.chk_use_proj.toggled.connect(self._on_check_changed)
        self.same_ell.toggled.connect(self._sync_same_ell)
        self.ell_src_combo.currentIndexChanged.connect(self._sync_same_ell)
        self._src = None
        self._dst = None
        self._rows = None
        self._hf = None
        self._masks = None
        self._quiet = False
        self._zone_s = None          # 源 y 带号识别结果（dict | None）
        self.cmp_table.setToolTip(
            "RMS=均方根残差（越小越好）；LOO=留一验证：轮流扣掉一个点用其余点预测它，"
            "反映外推可靠性。★ 为自动择优；『直接投影/固定差』是基线参照行。")
        self.name_edit.setToolTip("参数保存名；默认按 日期_方法代号 命名，参数库中可改名/删除/导出")
        self._zone_t = None          # 目标 y 带号识别结果
        self._sync_smart_grey()      # 初始灰显状态
        self.reload_models()

    def _sync_proj_enabled(self):
        """兼容入口：投影参数勾选/数据变化后的灰显统一走智能灰显。"""
        self._sync_smart_grey()

    def _sync_smart_grey(self, *_):
        """智能灰显（计算/导入/勾选后联动）。

        1. 源/目标为大地坐标（或空间直角坐标）时，对应侧投影参数不参与计算并灰显；
        2. 源/目标平面 y 含带号时自动识别分带与带号（3度带 24~45 / 6度带 13~23），
           该侧中央子午线自动锁定并灰显，标签显示识别结果。
        判别实现在 app.core.projection / autoselect，本方法只做控件联动。
        """
        from app.core.autoselect import detect_coord_kind
        from app.core.projection import detect_zone_on_column
        kind = getattr(self, "_kind", None)
        src = dst = None
        try:
            _n, src, dst, *_r = self._read_points()
            if kind is None:
                kind = (detect_coord_kind(src[:, :2]), detect_coord_kind(dst[:, :2]))
        except Exception:  # noqa: BLE001
            pass
        src_planar = kind is None or kind[0] == "planar"    # 未知（空表）不武断灰掉
        dst_planar = kind is None or kind[1] == "planar"
        self._zone_s = (detect_zone_on_column(src[:, 1])
                        if src is not None and kind and kind[0] == "planar" else None)
        self._zone_t = (detect_zone_on_column(dst[:, 1])
                        if dst is not None and kind and kind[1] == "planar" else None)
        zs, zt = self._zone_s, self._zone_t
        # 源侧：仅源为平面时参与（大地/空间直角源无需投影）
        for w in (self.proj_x0_s, self.proj_y0_s, self.h0_s_edit,
                  self.rb_rig_s, self.rb_app_s, self.b0_s_edit):
            w.setEnabled(src_planar)
        self.proj_l0_s.setEnabled(src_planar and zs is None)
        self.lbl_zone_s.setText(
            f"已识别源坐标含带号：{zs['band']} 带号{zs['zone']}，L0 自动锁定 {zs['l0']:g}°"
            if zs else "")
        if zs:
            self.proj_l0_s.setText(f"{zs['l0']:.8f}")
        # 目标侧：目标为平面且勾选"使用投影参数"时参与
        t_on = dst_planar and self.chk_use_proj.isChecked()
        for w in (self.proj_x0, self.proj_y0, self.h0_edit, self.rb_rig_cp,
                  self.rb_app_cp, self.b0_t_edit):
            w.setEnabled(t_on)
        self.proj_l0.setEnabled(t_on and zt is None)
        self.lbl_zone_t.setText(
            f"已识别目标坐标含带号：{zt['band']} 带号{zt['zone']}，L0 自动锁定 {zt['l0']:g}°"
            if zt else "")
        if zt:
            self.proj_l0.setText(f"{zt['l0']:.8f}")

    def _sync_same_ell(self):
        """同一椭球：目标椭球镜像原始椭球并变灰；取消后可独立选择。"""
        same = self.same_ell.isChecked()
        self.ell_dst_combo.setEnabled(not same)
        if same:
            self.ell_dst_combo.setCurrentText(self.ell_src_combo.currentText())

    def _sync_opt_grey(self):
        """按勾选灰显：三/四参数与椭球/投影无关；七参数链式需要投影参数。"""
        use_seven = self.chk_seven.isChecked()
        proj_g = self.proj_l0.parentWidget()
        ell_g = self.ell_src_combo.parentWidget()
        kind = getattr(self, "_kind", ("planar", "planar"))
        proj_g.setEnabled(not use_seven or kind[1] == "planar")
        ell_g.setEnabled(True)

    _FIT_LABEL2MODE = {"固定差法": "const", "平面拟合法（斜面）": "plane", "曲面拟合法": "quadratic"}

    def reload_models(self):
        """填充大地水准面模型下拉（可用模型从 models/ 探测）+ 按前提默认勾选。"""
        cur = self.geoid_combo.currentData()
        self.geoid_combo.blockSignals(True)
        self.geoid_combo.clear()
        self.geoid_combo.addItem("全部可用模型", {"auto": True})
        from app.core.geoid import available_models
        usable = 0
        for m in available_models(MODELS_DIR):
            if m["usable"]:
                self.geoid_combo.addItem(m["name"], {"path": m["path"]})
                usable += 1
        if cur is not None:
            idx = self.geoid_combo.findData(cur)
            if idx >= 0:
                self.geoid_combo.setCurrentIndex(idx)
        self.geoid_combo.blockSignals(False)
        self._sync_geoid_default(usable)

    def _sync_geoid_default(self, usable: int | None = None):
        """大地水准面默认勾选前提：椭球为地心坐标系（CGCS2000/WGS84）且有可用格网。

        包含测区由计算时校验（源点出界 → 格网行不可行并注明）。用户手动
        点击过勾选框（_geoid_touched）后不再自动改。
        """
        if getattr(self, "_geoid_touched", False):
            return
        if usable is None:
            from app.core.geoid import available_models
            usable = sum(1 for m in available_models(MODELS_DIR) if m["usable"])
        ell_ok = self.ell_src_combo.currentText() in ("CGCS2000", "WGS84")
        want = bool(usable) and ell_ok
        if want and not self.chk_geoid_model.isChecked():
            self.chk_geoid_model.blockSignals(True)
            self.chk_geoid_model.setChecked(True)
            self.chk_geoid_model.blockSignals(False)
        elif not want and self.chk_geoid_model.isChecked():
            self.chk_geoid_model.blockSignals(True)
            self.chk_geoid_model.setChecked(False)
            self.chk_geoid_model.blockSignals(False)
        tip = ("地心坐标系（CGCS2000/WGS84）且有可用格网模型时默认启用；"
               "格网不覆盖测区时计算会注明" if ell_ok else
               "当前为参心坐标系（如北京54/西安80），EGM 位势格网不适用，默认不启用")
        self.chk_geoid_model.setToolTip(tip)

    def import_csv(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择点对文件", "",
            "所有支持的点对文件 (*.cot *.csv *.txt *.xlsx *.xlsm);;"
            "GNSS手簿点对 (*.cot);;"
            "南方CASS点对 (*.txt);;"
            "南方坐标转换软件点对 (*.txt);;"
            "Excel 表格 (*.xlsx *.xlsm);;"
            "CSV 表格 (*.csv)")
        if not path:
            return
        self.load_file(path)

    def export_pairs(self):
        """导出当前表格点对：GNSS手簿.cot / 南方CASS .txt / 南方坐标转换软件 .txt / xlsx / csv。"""
        try:
            rows, flags, src_kind = self._collect_pair_rows()
        except ValueError as e:
            _quiet_warn(self, "导出失败", str(e))
            return
        if not rows:
            _quiet_info(self, "提示", "没有可导出的点对行")
            return
        path, sel = QFileDialog.getSaveFileName(
            self, "导出点对文件", "控制点对",
            "GNSS手簿点对 (*.cot);;"
            "南方CASS点对 (*.txt);;"
            "南方坐标转换软件点对 (*.txt);;"
            "Excel 表格 (*.xlsx);;"
            "CSV 表格 (*.csv)")
        if not path:
            return
        fmt = ("cot" if "手簿" in sel else "cass" if "CASS" in sel
               else "south" if "南方坐标转换" in sel else "xlsx" if "Excel" in sel else "csv")
        try:
            p = self._export_to(path, fmt, rows, flags, src_kind)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "导出失败", str(e))
            return
        _oplog().log("export_pairs", {"path": p, "fmt": fmt, "n": len(rows)})
        _quiet_info(self, "已导出", f"{fmt} 格式，{len(rows)} 点\n{p}")

    def _collect_pair_rows(self):
        """读点对表 → (rows, flags, src_kind)；rows=[(点名,源1,源2,源h,目标1,目标2,目标h)]。

        src_kind 按源两列量程判别（大地/平面），供 cot 导出校验与逆向语义。
        """
        from app.core.autoselect import detect_coord_kind
        import numpy as _np
        rows, flags = [], []
        for r in range(self.table.rowCount()):
            items = [self.table.item(r, c) for c in range(9)]
            texts = [(it.text().strip() if it else "") for it in items]
            if not (texts[1] or texts[4]):
                continue
            if not (texts[1] and texts[2] and texts[4] and texts[5]):
                raise ValueError(f"第 {r + 1} 行数据不完整：源与目标前两列都必须有值")
            try:
                vals = [float(texts[i]) if texts[i] else 0.0 for i in range(1, 7)]
            except ValueError as e:
                raise ValueError(f"第 {r + 1} 行坐标不是数值：{e}") from None
            rows.append((texts[0] or f"P{r + 1}", *vals))
            flags.append((self._flag_on(items[7]), self._flag_on(items[8])))
        if rows:
            a = _np.asarray([[r_[1], r_[2]] for r_ in rows], dtype=float)
            src_kind = detect_coord_kind(a)
        else:
            src_kind = "planar"
        return rows, flags, src_kind

    def _export_to(self, path, fmt, rows=None, flags=None, src_kind=None) -> str:
        """按格式落盘（对话框独立，供测试与批量调用）。"""
        if rows is None:
            rows, flags, sk = self._collect_pair_rows()
            src_kind = src_kind or sk
        from app.common import pair_io
        allowed = {"south": ("txt",), "cass": ("txt",), "cot": ("cot",),
                   "xlsx": ("xlsx",), "csv": ("csv",)}.get(fmt, (fmt,))
        ext = Path(str(path)).suffix.lower().lstrip(".")
        if ext not in allowed:
            path = f"{path}.{allowed[0]}"
        return pair_io.export_pairs(path, fmt, rows, flags,
                                    src_kind=src_kind or "planar")

    def load_file(self, path: str):
        """自动识别：手簿 .cot / CASS 公共点(冒号结构) / 通用表格(xlsx·csv·txt)。"""
        from app.common import pair_io
        if Path(path).suffix.lower() == ".cot":
            try:
                pts = pair_io.parse_cot(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            self._load_cot(pts)
        elif pair_io.looks_like_cass(path):
            try:
                pairs = pair_io.parse_cass(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            self._load_cass(pairs)
        elif pair_io.looks_like_south_pairs(path):
            try:
                sp = pair_io.parse_south_pairs(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            self._load_south([(p.name, p.x1, p.y1, p.h1, p.x2, p.y2, p.h2) for p in sp], "南方")
        else:
            try:
                pts = read_points_csv(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            self._fill_pairs(pts)
        self._sync_smart_grey()   # 导入后立即联动灰显与带号识别

    def _fill_pairs(self, pts):
        self.clear_rows()
        self.table.setRowCount(0)
        for p in pts:
            r = self.table.rowCount()
            self.table.insertRow(r)
            has_bl = p.get("B") is not None and p.get("L") is not None
            vals = [p.get("name"),
                    p.get("L") if has_bl else p.get("x"), p.get("B") if has_bl else p.get("y"),
                    p.get("H_ell") if has_bl else p.get("h"),
                    p.get("L") if has_bl else p.get("x"), p.get("B") if has_bl else p.get("y"),
                    p.get("H") if has_bl else p.get("H")]
            if not has_bl:
                for j, key in enumerate(("x2", "y2", "H")):
                    if p.get(key) is not None:
                        vals[4 + j] = p[key]
            for j, val in enumerate(vals):
                self.table.setItem(r, j, QTableWidgetItem("" if val is None else str(val)))
            self.table.setItem(r, 7, QTableWidgetItem("Y"))
            self.table.setItem(r, 8, QTableWidgetItem("Y"))
        self.table.resizeColumnsToContents()

    def _load_cot(self, pts):
        """手簿 .cot：源=WGS84(B,L,H)，目标=地方平面(x,y)+正常高。"""
        self.clear_rows()
        self.table.setRowCount(0)
        for i, p in enumerate(pts):
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = [p.name, p.l_deg, p.b_deg, p.h_ell, p.x, p.y, p.h_normal]
            for j, val in enumerate(vals):
                txt = str(val) if isinstance(val, str) else (f"{val:.9f}" if isinstance(val, float) else val)
                self.table.setItem(r, j, QTableWidgetItem(txt))
            for j, on in ((7, p.use_pos), (8, p.use_h)):
                it = QTableWidgetItem()
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                self.table.setItem(r, j, it)
        self.table.resizeColumnsToContents()
        _oplog().log("import_cot", {"n": len(pts),
                                    "use_pos": sum(1 for p in pts if p.use_pos),
                                    "use_h": sum(1 for p in pts if p.use_h)})

    def _load_cass(self, pairs):
        """南方 CASS 公共点：旧(x,y,h) → 新(x,y,h)，平面点对（等高时为二维情形）。"""
        self._load_south([(f"CASS{i+1}", p.x1, p.y1, p.h1, p.x2, p.y2, p.h2)
                          for i, p in enumerate(pairs)], "CASS")

    def _load_south(self, rows_in, tag="南方"):
        """南方坐标转换软件公共点对：旧(x,y,h) → 新(x,y,h)（7 列同名格式）。"""
        self.clear_rows()
        self.table.setRowCount(0)
        for i, row in enumerate(rows_in):
            r = self.table.rowCount()
            self.table.insertRow(r)
            vals = list(row)
            for j, val in enumerate(vals):
                txt = str(val) if isinstance(val, str) else (f"{val:.4f}" if isinstance(val, float) else val)
                self.table.setItem(r, j, QTableWidgetItem(txt))
            for j in (7, 8):
                it = QTableWidgetItem()
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked)
                self.table.setItem(r, j, it)
        self.table.resizeColumnsToContents()
        _oplog().log("import_south_pairs", {"tag": tag, "n": len(rows_in)})

    def add_row(self):
        self.table.insertRow(self.table.rowCount())

    def del_row(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def clear_rows(self):
        self.table.setRowCount(0)
        self.table.insertRow(0)  # 保留一行空行供直接键入

    @staticmethod
    def _flag_on(item) -> bool:
        if item is None:
            return True
        # 文本旗标（旧 Y/N 与手输）优先：PySide6 默认 flags 即含 UserCheckable，
        # 勾选态重构后空文本项才代表复选框；带文本的项按 Y/N 解析
        t = item.text().strip()
        if t:
            return not t.upper().startswith(("N", "否", "0"))
        if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            return item.checkState() == Qt.Checked
        return True

    def _read_points(self):
        src, dst, names = [], [], []
        pos_mask, h_mask = [], []
        self._pt_rows = []          # 数组下标 → 表格行号（残差回填用）
        for r in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(r, c)
                if it is None or not it.text().strip():
                    return None
                try:
                    return float(it.text())
                except ValueError:
                    return None
            x1, y1, h1 = cell(1), cell(2), cell(3)
            x2, y2, h2 = cell(4), cell(5), cell(6)
            if x1 is None or y1 is None or x2 is None or y2 is None:
                continue
            name_it = self.table.item(r, 0)
            names.append(name_it.text().strip() if name_it and name_it.text() else f"P{r+1}")
            src.append((x1, y1))
            dst.append((x2, y2))
            h1 = h1 if h1 is not None else 0.0
            h2 = h2 if h2 is not None else 0.0
            src[-1] = (x1, y1, h1)
            dst[-1] = (x2, y2, h2)
            pos_mask.append(self._flag_on(self.table.item(r, 7)))
            h_mask.append(self._flag_on(self.table.item(r, 8)))
            self._pt_rows.append(r)
        if len(src) < 2:
            raise ValueError("至少需要 2 个完整控制点对")
        if not any(pos_mask):
            raise ValueError("“用平面”全部为 N：无点参与平面参数计算")
        arr_s = np.array([(a, b, h) for a, b, h in src])
        arr_d = np.array([(a, b, h) for a, b, h in dst])
        return names, arr_s, arr_d, pos_mask, h_mask

    def compute(self, auto_check: bool = True):
        """计算与比选。auto_check=True（主按钮）：自动勾选合适选项后按勾选过滤；
        False（各选项旁的"计算"按钮）：保持用户勾选，按勾选过滤。"""
        from app.core import autoselect
        try:
            names, src, dst, pos_mask, h_mask = self._read_points()
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "输入有误", str(e))
            return
        kind = autoselect.detect_coord_kind(src[:, :2])
        kind2 = autoselect.detect_coord_kind(dst[:, :2])
        self._kind = (kind, kind2)
        self._sync_smart_grey()          # 智能灰显 + 带号自动识别（联动源/目标侧投影参数）
        ztxt = ""
        if self._zone_s:
            ztxt += f"；源含带号（{self._zone_s['band']} 带号{self._zone_s['zone']}，L0={self._zone_s['l0']:g}°）"
        if self._zone_t:
            ztxt += f"；目标含带号（{self._zone_t['band']} 带号{self._zone_t['zone']}，L0={self._zone_t['l0']:g}°）"
        self.lbl_kind.setText(f"坐标类型：源={kind} → 目标={kind2}{ztxt}（lonlat=(经度,纬度)）")
        try:
            proj = self._proj_values()
        except ValueError as e:  # noqa: BLE001
            _quiet_warn(self, "投影参数有误", str(e))
            return
        grids = self._load_geoid_grids() if self.chk_geoid_model.isChecked() else None
        from app.core.ellipsoid import get_ellipsoid
        ell_src = get_ellipsoid(self.ell_src_combo.currentText())
        ell_dst = (ell_src if self.same_ell.isChecked()
                   else get_ellipsoid(self.ell_dst_combo.currentText()))
        if ell_src is None or ell_dst is None:
            _quiet_warn(self, "椭球无效", "所选椭球不存在（可能已被删除），请重新选择。")
            return
        try:
            rows = autoselect.compare_methods(src[:, :2], dst[:, :2], src[:, 2], dst[:, 2],
                                              kind, kind2, ell=ell_src, ell_dst=ell_dst, l0=None,
                                              force_mode=None,
                                              pos_mask=pos_mask, h_mask=h_mask,
                                              plane_force=None, proj=proj,
                                              geoid_grids=grids, no_height=False,
                                              height_modes=(["const"] if self.chk_precise_h.isChecked()
                                                            else ["const", "plane", "quadratic"]))
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "计算失败", str(e))
            return
        self._src, self._dst, self._rows, self._kind = src, dst, rows, (kind, kind2)
        self._names, self._masks = names, (pos_mask, h_mask)
        self._geoid_grids = grids or []
        if auto_check:
            self._auto_check(rows)
        self._refresh_results()
        _oplog().log("param_fit", {"n": len(names), "source_kind": kind, "target_kind": kind2,
                                   "precise_xy": self.chk_precise_xy.isChecked(),
                                   "precise_h": self.chk_precise_h.isChecked(),
                                   "geoid": self.geoid_combo.currentText(),
                                   "selected": (self._sel or {}).get("method"),
                                   "selected_h": (self._sel_h or {}).get("method")})

    def _mutex_34(self, on: bool):
        """三/四参数手动互斥：勾选一方时取消另一方（允许双方全不勾）。"""
        if self._mutexing or not on:
            return
        self._mutexing = True
        try:
            other = (self.rb_planar3 if self.sender() is self.rb_planar4
                     else self.rb_planar4)
            if other.isChecked():
                other.blockSignals(True)
                other.setChecked(False)
                other.blockSignals(False)
            self._on_check_changed()
        finally:
            self._mutexing = False

    def _compute_manual(self):
        """各选项旁的"计算"按钮：保持用户勾选直接解算。"""
        self.compute(auto_check=False)

    def _on_check_changed(self):
        """勾选变化：已算过则按新勾选重算过滤。"""
        if getattr(self, "_rows", None):
            self.compute(auto_check=False)

    def _checks(self) -> "_cp.CpChecks":
        """从控件读取勾选状态（策略判别全部在 logic 层）。"""
        return _cp.CpChecks(
            planar4=self.rb_planar4.isChecked(), planar3=self.rb_planar3.isChecked(),
            seven=self.chk_seven.isChecked(),
            fit_plane=self.chk_fit_plane.isChecked(), fit_quad=self.chk_fit_quad.isChecked(),
            geoid_model=self.chk_geoid_model.isChecked(),
            precise_xy=self.chk_precise_xy.isChecked(), precise_h=self.chk_precise_h.isChecked(),
            use_proj=self.chk_use_proj.isChecked())

    def _auto_check(self, rows):
        """主按钮：按比选结果自动勾选合适选项（不触发信号）。"""
        c2 = _cp.auto_check(rows, self._checks())
        widgets = [self.rb_planar4, self.rb_planar3, self.chk_seven,
                   self.chk_fit_plane, self.chk_fit_quad, self.chk_geoid_model,
                   self.chk_use_proj]
        for w in widgets:
            w.blockSignals(True)
        try:
            self.rb_planar4.setChecked(c2.planar4)
            self.rb_planar3.setChecked(c2.planar3)
            self.chk_seven.setChecked(c2.seven)
            self.chk_fit_plane.setChecked(c2.fit_plane)
            self.chk_fit_quad.setChecked(c2.fit_quad)
            self.chk_geoid_model.setChecked(c2.geoid_model)
            self.chk_use_proj.setChecked(c2.use_proj)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _row_visible(self, r) -> bool:
        return _cp.visible(r, self._checks())

    def _refresh_results(self):
        """按勾选过滤 → 选优（平面 RMS 最优 + 高程 RMS 最优各一）→ 明细/面板/残差。"""
        rows = self._rows or []
        sel, sel_h = _cp.select(rows, self._checks())
        for r in rows:
            r["selected"] = (r is sel)
        self._sel, self._sel_h = sel, sel_h
        self._fill_cmp_table(_cp.visible_rows(rows, self._checks()))
        self._fill_param_panels()
        self._fill_residuals()
        self._update_detail()
        if not sel and not sel_h:
            _quiet_warn(self, "无可行方法",
                        "当前勾选下无可行的平面/高程转换方法（见对比表说明）。请检查勾选与投影参数。")
        elif not sel:
            _quiet_warn(self, "提示",
                        "无可行平面转换方法；可单独保存高程/大地水准面参数（保存按钮）。")

    def _fill_cmp_table(self, vis):
        data = []
        for r in vis:
            data.append([_cp.method_label(r["method"]), "是" if r["feasible"] else "否", r["n"],
                         r.get("rms_xy"), r.get("rms_h"), r.get("loo_rms_h"),
                         "★" if r.get("selected") else "", r.get("note", "")])
        _fill_table(self.cmp_table, ["方法", "可行", "点数", "RMS平面(m)", "RMS高程(m)",
                                     "LOO高程(m)", "选用", "说明"], data)

    def _fill_param_panels(self):
        """勾选项的参数明细面板（点开可见）——文案构建在 logic 层。"""
        s_used, d_used = self._used_subsets()
        proj = self._proj_values()
        kind = getattr(self, "_kind", None) or ("planar", "planar")
        self.lbl_p_4.setText(_cp.planar_panel_text("planar4", s_used, d_used, proj, kind))
        self.lbl_p_3.setText(_cp.planar_panel_text("planar3", s_used, d_used, proj, kind))
        self.lbl_p_7.setText(_cp.planar_panel_text(_cp.seven_method_for(kind), s_used, d_used, proj, kind))
        hf_rows = {r["method"]: r for r in (self._rows or []) if r.get("heightfit")}
        self.lbl_p_plane.setText(_cp.height_panel_text(hf_rows.get("heightfit:plane")))
        self.lbl_p_quad.setText(_cp.height_panel_text(hf_rows.get("heightfit:quadratic")))
        self.lbl_p_geoid.setText(_cp.geoid_panel_text(getattr(self, "_geoid_grids", []),
                                                      self._rows or []))

    def _fill_residuals(self):
        """把所选平面/高程方法的逐点残差写进表格最后三列（映射在 logic 层）。"""
        for r in range(self.table.rowCount()):
            for c in (9, 10, 11):
                self.table.setItem(r, c, QTableWidgetItem(""))
        for trow, col, text in _cp.residual_cells(self.table.rowCount(),
                                                  getattr(self, "_pt_rows", []),
                                                  getattr(self, "_sel", None),
                                                  getattr(self, "_sel_h", None)):
            self.table.setItem(trow, col, QTableWidgetItem(text))

    def open_geoid_dir(self):
        """打开大地水准面模型存放文件夹。"""
        MODELS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(MODELS_DIR))

    def _proj_values(self):
        """读取投影参数栏：{"l0": 十进制度, "x0_m"/"y0_m": m, "h0": 投影面大地高,
        "rigorous": 严密/近似, "ell": 原始椭球对象, "ell_dst": 目标椭球对象,
        "proj_src": 源侧投影参数（逆向链用）}。"""
        from app.core import anglefmt
        from app.core.ellipsoid import get_ellipsoid
        try:
            h0 = float(self.h0_edit.text())
        except ValueError as e:
            raise ValueError(f"投影面大地高需为数值：{e}") from e
        ell_dst = (get_ellipsoid(self.ell_src_combo.currentText())
                   if self.same_ell.isChecked()
                   else get_ellipsoid(self.ell_dst_combo.currentText()))
        bt = self.b0_t_edit.text().strip()
        try:
            b0_manual = math.degrees(anglefmt.parse_flexible(bt, "dms")) if bt else None
        except ValueError as e:
            raise ValueError(f"目标侧平均纬度有误：{e}") from None
        return {"l0": math.degrees(anglefmt.parse_flexible(self.proj_l0.text(), "dms")),
                "x0_m": float(self.proj_x0.text()) * 1000.0,
                "y0_m": float(self.proj_y0.text()) * 1000.0,
                "h0": h0, "rigorous": self.rb_rig_cp.isChecked(),
                "b0_manual": b0_manual,
                "ell": get_ellipsoid(self.ell_src_combo.currentText()),
                "ell_dst": ell_dst,
                "proj_src": self._proj_src_values()}

    def _proj_src_values(self):
        """读取源侧投影参数（源=工程平面坐标时用于反算大地坐标）。

        带号识别命中时 l0 以识别值为准；中央子午线留空 → l0=None（逆向方法不可行，
        由比选给出说明）。"""
        from app.core import anglefmt
        from app.core.ellipsoid import get_ellipsoid
        t = self.proj_l0_s.text().strip()
        try:
            l0 = math.degrees(anglefmt.parse_flexible(t, "dms")) if t else None
            x0t = self.proj_x0_s.text().strip()
            y0t = self.proj_y0_s.text().strip()
            h0t = self.h0_s_edit.text().strip()
        except ValueError as e:
            raise ValueError(f"源侧投影参数有误：{e}") from e
        if getattr(self, "_zone_s", None):
            l0 = float(self._zone_s["l0"])
        bt = self.b0_s_edit.text().strip()
        try:
            b0_manual = math.degrees(anglefmt.parse_flexible(bt, "dms")) if bt else None
        except ValueError as e:
            raise ValueError(f"源侧平均纬度有误：{e}") from None
        return {"l0": l0,
                "x0_m": (float(x0t) * 1000.0 if x0t else 0.0),
                "y0_m": (float(y0t) * 1000.0 if y0t else 500000.0),
                "h0": (float(h0t) if h0t else 0.0),
                "rigorous": self.rb_rig_s.isChecked(),
                "b0_manual": b0_manual,
                "ell": get_ellipsoid(self.ell_src_combo.currentText())}

    def _load_geoid_grids(self):
        """按大地水准面下拉加载格网：自动判断=全部可用模型；不使用=None。"""
        from app.core.geoid import load_grid, GridModel, available_models
        gsel = self.geoid_combo.currentData()
        if not gsel:
            return None
        paths = ([m["path"] for m in available_models(MODELS_DIR) if m["usable"]]
                 if gsel.get("auto") else [gsel["path"]])
        grids = []
        for pth in paths:
            try:
                grids.append((Path(pth).stem, GridModel(load_grid(pth))))
            except Exception:  # noqa: BLE001
                continue
        return grids or None

    def _used_subsets(self):
        """按"用平面"掩码取参与参数解算的源/目标子集。"""
        pos_mask = (np.asarray(self._masks[0], dtype=bool)
                    if self._masks and self._masks[0] is not None else None)
        s_used = self._src[pos_mask] if pos_mask is not None else self._src
        d_used = self._dst[pos_mask] if pos_mask is not None else self._dst
        return s_used, d_used

    def _refit_params(self, m):
        """按所选方法重算参数，返回 (updates, text)——实现在 logic 层。"""
        s_used, d_used = self._used_subsets()
        return _cp.refit(m, s_used, d_used, self._proj_values(),
                         getattr(self, "_kind", ("planar", "planar")))

    def _update_detail(self):
        """信息栏：展示所选方法（logic 层 select 的结果）的参数与残差。"""
        sel = getattr(self, "_sel", None)
        sel_h = getattr(self, "_sel_h", None)
        if not self._rows or (sel is None and sel_h is None):
            notes = [r.get("note", "") for r in self._rows or [] if r.get("note")]
            self.detail.setPlainText("无可行方法。\n" + "\n".join(notes))
            return
        parts = []
        if sel is not None:
            r = sel
            head = (f"【{_cp.method_label(r['method'])}】（代号 {r['method']}）"
                    f"    参与点数：{r['n']}")
            if r.get("rms_xy") is not None:
                head += f"    RMS平面={r['rms_xy']:.4f} m"
            try:
                _u, ptext = self._refit_params(r["method"])
                if ptext:
                    head += "\n" + ptext
            except Exception as e:  # noqa: BLE001
                head += f"\n（参数明细计算失败：{e}）"
            parts.append(head)
        if sel_h is not None and sel_h is not sel:
            r = sel_h
            head = (f"【{_cp.method_label(r['method'])}】（代号 {r['method']}）"
                    f"    参与点数：{r['n']}")
            if r.get("rms_h") is not None:
                head += f"    RMS高程={r['rms_h']:.4f} m"
            if r.get("loo_rms_h") is not None:
                head += f"    LOO高程={r['loo_rms_h']:.4f} m"
            hf = r.get("heightfit")
            if hf is not None:
                head += (f"\n系数={[round(float(c), 6) for c in hf.coef]}    "
                         f"中心={[round(float(c), 3) for c in hf.center]}")
            parts.append(head)
        note = (sel or sel_h).get("note", "")
        if note:
            parts.append("说明：" + note)
        self.detail.setPlainText("\n".join(parts))
        if sel is not None:
            self.name_edit.setText(f"{datetime.now().strftime('%m%d_%H%M')}_{sel['method']}")
        elif sel_h is not None:
            self.name_edit.setText(f"{datetime.now().strftime('%m%d_%H%M')}_{sel_h['method'].split(':')[-1]}")

    def save_param(self):
        from app.common.params import ParamApplier  # noqa: F401  (确认依赖已装)
        if not self._rows:
            _quiet_info(self, "提示", "请先执行计算与比选")
            return
        sel = [r for r in self._rows if r.get("selected")]
        sel_h = getattr(self, "_sel_h", None)
        if not sel and not sel_h:
            _quiet_info(self, "提示", "无可用方法（平面与高程均无可行结果）")
            return
        r = sel[0] if sel else {"method": "geoid", "n": sel_h.get("n_h", 0),
                                "rms_xy": None, "rms_h": sel_h.get("rms_h"),
                                "loo_rms_h": sel_h.get("loo_rms_h"), "note": sel_h.get("note", "")}
        m = r["method"]
        kind1, kind2 = self._kind
        name = self.name_edit.text().strip() or f"参数_{datetime.now().strftime('%m%d%H%M')}"
        try:
            updates = {}
            if sel:
                s_used, d_used = self._used_subsets()
                updates, _text = _cp.refit(m, s_used, d_used, self._proj_values(),
                                           getattr(self, "_kind", ("planar", "planar")))
            d = _cp.build_param_doc(name, self._kind, m, updates, getattr(self, "_sel_h", None),
                                    self._names, self.chk_geoid.isChecked())
            d["accuracy"].update({"rms_xy_m": r.get("rms_xy"), "rms_h_m": r.get("rms_h"),
                                  "loo_rms_h_m": r.get("loo_rms_h"), "n_points": r["n"],
                                  "note": r.get("note", "")})
            path = ParamLibrary(PARAMS_DIR).save(d)
            _oplog().log("param_save", {"name": name, "kind": m, "file": path})
            _quiet_info(self, "已保存", f"参数已保存：{path}")
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "保存失败", str(e))

class PhotoPosPage(QWidget):
    """无人机照片 POS 处理页。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("无人机照片 POS 高程转换", "先「计算」预览转换结果，确认后「开始处理」回写 GPSAltitude（可选同时转换平面坐标）；"
                                                    "_POS备份/ 仅首次保存原始照片（POS/XMP 原样），之后永不改动；逐张台账可回滚。"))
        g1 = QGroupBox("1. 选择目录并扫描")
        gv1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        self.roots_list = QListWidget()
        self.roots_list.setMaximumHeight(96)     # 多目录放不下时用内置滑块
        self.btn_add_root = QPushButton("添加目录")
        self.btn_del_root = QPushButton("移除选中")
        self.btn_scan = QPushButton("扫描照片")
        r1.addWidget(self.roots_list, 1)
        vr1 = QVBoxLayout()
        vr1.addWidget(self.btn_add_root)
        vr1.addWidget(self.btn_del_root)
        vr1.addWidget(self.btn_scan)
        vr1.addStretch(1)
        r1.addLayout(vr1)
        r1.addWidget(self.btn_scan)
        gv1.addLayout(r1)
        self.scan_label = QLabel("未扫描")
        self.scan_label.setObjectName("muted")
        gv1.addWidget(self.scan_label)
        v.addWidget(g1)
        g2 = QGroupBox("2. 高程转换方式")
        gv2 = QGridLayout(g2)
        self.mode_combo = QComboBox()
        self.reload_modes()
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSuffix(" m")
        self.xmp_check = QCheckBox("同时更新 XMP AbsoluteAltitude")
        self.xmp_check.setChecked(True)
        gv2.addWidget(QLabel("方式"), 0, 0)
        gv2.addWidget(self.mode_combo, 0, 1)
        gv2.addWidget(QLabel("常数偏移"), 0, 2)
        gv2.addWidget(self.offset_spin, 0, 3)
        gv2.addWidget(self.xmp_check, 0, 4)
        self.chk_plane = QCheckBox("同时转换平面坐标（需选择含平面转换的参数，默认仅处理高程）")
        gv2.addWidget(self.chk_plane, 1, 0, 1, 5)
        v.addWidget(g2)
        g3 = QGroupBox("3. 执行")
        gv3 = QVBoxLayout(g3)
        r3 = QHBoxLayout()
        self.btn_preview = QPushButton("计算")
        self.btn_apply = QPushButton("开始处理")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_rollback = QPushButton("从备份回滚")
        self.btn_preview.setToolTip("只计算不写盘：逐张预览换算结果，确认无误再开始处理")
        self.btn_apply.setToolTip("把换算后的高程写回照片 XMP/EXIF；首次运行自动在 _POS备份/ 保存原始照片")
        self.btn_rollback.setToolTip("用 _POS备份/ 中的原始照片还原所选目录的全部照片")
        r3.addWidget(self.btn_preview)
        r3.addWidget(self.btn_apply)
        r3.addWidget(self.btn_cancel)
        r3.addWidget(self.btn_rollback)
        r3.addStretch(1)
        gv3.addLayout(r3)
        self.bar = QProgressBar()
        gv3.addWidget(self.bar)
        self.result_table = QTableWidget(0, 10)
        self.result_table.setHorizontalHeaderLabels(["序号", "文件", "纬度", "经度", "处理前m", "转换值m",
                                                    "处理后m", "转换方式", "状态", "XMP"])
        self.result_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.result_table.setAlternatingRowColors(True)
        gv3.addWidget(self.result_table)
        v.addWidget(g3, 3)
        # 信号
        self.btn_add_root.clicked.connect(self.pick_dir)
        self.btn_del_root.clicked.connect(self.remove_root)
        self.btn_scan.clicked.connect(self.scan)
        self.btn_preview.clicked.connect(self.preview)
        self.btn_apply.clicked.connect(self.start)
        self.btn_apply.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_rollback.clicked.connect(self.rollback)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self._quiet = False
        self.worker = None
        self.scan_worker = None
        self.rollback_worker = None

    def reload_modes(self):
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        from app.core.geoid import available_models
        for m in available_models(MODELS_DIR):
            if m["usable"]:
                self.mode_combo.addItem(f"格网模型: {m['name']}", {"mode": "geoid", "path": m["path"]})
        lib = ParamLibrary(PARAMS_DIR).list()
        for p in lib:
            try:
                d = ParamLibrary(PARAMS_DIR).load(p["name"])
            except Exception:  # noqa: BLE001
                continue
            if d.get("heightfit"):
                tag = "（大地水准面）" if d.get("use_geoid") else ""
                self.mode_combo.addItem(f"拟合参数: {d['name']}{tag}", {"mode": "param", "param": d})
        self.mode_combo.addItem("常数偏移", {"mode": "offset"})
        self.mode_combo.setItemData(self.mode_combo.count() - 1,
                                    "把全部照片高程统一加/减一个常数（如测区高程异常近似为常数时的简单换算）",
                                    Qt.ToolTipRole)
        self.mode_combo.blockSignals(False)

    def showEvent(self, e):
        """页面显示时刷新参数下拉（参数可能在控制点页/参数库刚保存）。"""
        super().showEvent(e)
        try:
            self.reload_modes()
        except Exception:  # noqa: BLE001
            pass

    def _on_mode_changed(self):
        """选择转换参数时：按参数记录联动 XMP 勾选与平面转换勾选（均可手动更改）。"""
        data = self.mode_combo.currentData() or {}
        if data.get("mode") == "param":
            pd = data.get("param") or {}
            self.xmp_check.setChecked(bool(pd.get("use_geoid", False)))
            # 含七参数的转换参数默认同时转换平面坐标（其余默认仅高程，用户可改）
            self.chk_plane.setChecked(bool(pd.get("seven")))

    def pick_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择照片根目录")
        if d:
            if d not in self._roots():
                self.roots_list.addItem(d)
            self.roots_list.setCurrentRow(self.roots_list.count() - 1)

    def _roots(self) -> list:
        return [self.roots_list.item(i).text() for i in range(self.roots_list.count())]

    def remove_root(self):
        for row in sorted({i.row() for i in self.roots_list.selectedIndexes()}, reverse=True):
            self.roots_list.takeItem(row)

    def scan(self):
        roots = self._roots()
        if not roots:
            _quiet_info(self, "提示", "请先添加目录")
            return
        if self.scan_worker is not None and self.scan_worker.isRunning():
            _quiet_info(self, "提示", "扫描进行中，请稍候")
            return
        self.scan_label.setText("扫描中…")
        self.scan_worker = ScanWorker(roots)
        self.scan_worker.done.connect(self._scan_done)
        self.scan_worker.start()

    def _scan_done(self, n, err):
        self.scan_label.setText(f"共 {n} 张照片" if not err else f"扫描失败：{err}")

    def _height_spec(self) -> dict:
        """把界面选择打包为转换方式描述（格网解码在工作线程内进行，避免大文件冻结 UI）。"""
        data = self.mode_combo.currentData() or {"mode": "offset"}
        spec = dict(data)
        spec["offset"] = self.offset_spin.value()
        return spec

    def _build_worker(self, dry_run: bool):
        """校验并构造批处理工作线程（dry_run=True 仅计算预览）。"""
        if self.worker is not None and self.worker.isRunning():
            _quiet_info(self, "提示", "批处理进行中，请先取消或等待完成")
            return None
        if self.rollback_worker is not None and self.rollback_worker.isRunning():
            _quiet_info(self, "提示", "回滚进行中，请等待完成后再批处理")
            return None
        roots = self._roots()
        if not roots:
            _quiet_info(self, "提示", "请先添加目录")
            return None
        spec = self._height_spec()
        plane_fn = plane_name = None
        if self.chk_plane.isChecked():
            plane_fn, plane_name, perr = _pspec.plane_fn_from_param(
                self.plane_param_combo.currentData())
            if perr:
                _quiet_warn(self, "无法转换平面坐标", perr)
                return None
        worker = PhotoBatchWorker(roots, spec, update_xmp=self.xmp_check.isChecked(),
                                  plane_fn=plane_fn, plane_name=plane_name, dry_run=dry_run)
        worker.progress.connect(self._on_progress)
        worker.finished_ok.connect(self._on_preview_done if dry_run else self._on_done)
        worker.failed.connect(self._on_failed)
        return worker

    def preview(self):
        """计算预览：不备份、不回写，结果表格供用户确认。"""
        worker = self._build_worker(dry_run=True)
        if worker is None:
            return
        self.worker = worker
        self.result_table.setRowCount(0)
        self.bar.setValue(0)
        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker.start()

    def start(self):
        """开始处理：按计算结果正式回写照片（备份仅首次保存原始件）。"""
        if self.worker is not None and self.worker.isRunning():
            _quiet_info(self, "提示", "批处理进行中，请先取消或等待完成")
            return
        worker = self._build_worker(dry_run=False)
        if worker is None:
            return
        self.worker = worker
        self.result_table.setRowCount(0)
        self.bar.setValue(0)
        self.btn_preview.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker.start()

    def cancel(self):
        if self.worker:
            self.worker.cancel()

    def _on_progress(self, done, total, current):
        self.bar.setMaximum(total)
        self.bar.setValue(done)

    def _on_preview_done(self, res: dict):
        self.btn_preview.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._fill_result_table(res)
        n_xmp = sum(1 for r in res["rows"] if r.get("xmp_ok"))
        self.xmp_check.setEnabled(n_xmp > 0)
        if n_xmp == 0:
            self.xmp_check.setChecked(False)
        self.xmp_check.setToolTip("无 XMP 照片不可识别时更新将被跳过" if n_xmp == 0 else "")
        _quiet_info(self, "计算完成",
                                f"共 {res['total']} 张：可转换 {res['ok']}，失败 {res['failed']}，跳过 {res['skipped']}\n"
                                "确认无误后点击「开始处理」写入照片。")

    def _fill_result_table(self, res: dict):
        rows = res["rows"]
        self.result_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r.get("seq"), Path(r.get("path", "")).name, r.get("lat"), r.get("lon"),
                    r.get("h_before"), r.get("v"), r.get("h_after"), r.get("mode"),
                    r.get("status"), r.get("error", "")]
            for j, val in enumerate(vals):
                it = QTableWidgetItem("" if val is None else str(val))
                if r.get("status") == "ok":
                    it.setForeground(QColor("#2a6f8e"))
                elif r.get("status") in ("write_failed", "out_of_range", "no_pos"):
                    it.setForeground(QColor("#c96f4a"))
                elif r.get("status") == "预览":
                    it.setForeground(QColor("#6b7a83"))
                self.result_table.setItem(i, j, it)
        self.result_table.resizeColumnsToContents()

    def _on_done(self, res: dict):
        self.btn_preview.setEnabled(True)
        self.btn_apply.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self._fill_result_table(res)
        _oplog().log("photo_batch", {"root": res["root"], "total": res["total"], "ok": res["ok"],
                                     "failed": res["failed"], "skipped": res["skipped"],
                                     "ledger": res["ledger_csv"], "dry_run": res.get("dry_run", False)})
        self.reload_modes()
        _quiet_info(self, "处理完成",
                                f"共 {res['total']} 张：成功 {res['ok']}，失败 {res['failed']}，跳过 {res['skipped']}\n"
                                f"台账：{res['ledger_csv']}")

    def _on_failed(self, err):
        self.btn_preview.setEnabled(True)
        self.btn_apply.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        _quiet_warn(self, "处理失败", err)

    def rollback(self):
        if self.rollback_worker is not None and self.rollback_worker.isRunning():
            _quiet_info(self, "提示", "回滚进行中，请稍候")
            return
        if self.worker is not None and self.worker.isRunning():
            _quiet_info(self, "提示", "批处理进行中，请先取消或等待完成后再回滚")
            return
        roots = self._roots()
        if not roots:
            _quiet_info(self, "提示", "请先添加目录")
            return
        self.rollback_worker = RollbackWorker(roots)
        self.rollback_worker.finished_ok.connect(self._on_rollback)
        self.rollback_worker.start()

    def _on_rollback(self, r: dict):
        _oplog().log("photo_rollback", {"roots": r.get("roots", []), "restored": r["restored"],
                                       "missing_backup": r["missing_backup"]})
        roots_txt = "、".join(r.get("roots", [])) if r.get("roots") else "所选目录"
        _quiet_info(self, "回滚完成",
                    f"已还原 {r['restored']} 张（备份缺失 {r['missing_backup']}）\n"
                    f"目录：{roots_txt}")


# ============================================================ 多项式转换

POLY_DISCLAIMER = ("适用性说明：当重合点分布均匀、数量足够，且目标坐标系的精度比源坐标系的精度高时，"
                   "可以得到较高的转换精度。但当重合点数量较少且分布不均匀时，转换精度降低，"
                   "尤其不适用于转换外推。（除非知道在做什么，否则一般不用这个）")

# (显示名, 参数kind, 是否三维, source_kind, 点对表头) —— 旧三态模型常量已由
# "二维/三维 + 源/目标空间自动决策"（_sync_poly_grey / fit_and_save）取代。

_MODEL_HEADERS = ["点号", "源x₁(m)", "源y₁(m)", "源h/H", "目标x₂(m)", "目标y₂(m)", "目标h/H"]


class OsgbPage(QWidget):
    """OSGB 模型坐标/高程转换页。

    设计约束（来自项目契约）：
      - 转换算法来自已实测的 \`app.osgb\` 模块，不在此重复实现
      - 实算交给 \`app.osgb.flash\`（内存态 C++ 工具驱动，实测 1537 瓦片 / 23 s）
      - SRSOrigin 保持不动：顶点承载世界坐标位移，局部坐标系不变
      - 绝不覆盖输入：输出目录与模型目录相同会被拒绝
    """

    MODES = [("xyz", "平面 + 高程（XY 与 Z 都转换）"),
             ("xy", "仅平面坐标（只改 XY，Z 不变）"),
             ("z", "仅高程（只改 Z，XY 不变）")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._result = None
        v = QVBoxLayout(self)
        v.addWidget(_title(
            "OSGB 模型坐标转换",
            "对倾斜摄影 OSGB 模型逐顶点做坐标/高程转换。"
            "顶点承载世界坐标位移，SRSOrigin 保持不变（局部坐标系不动）。\n"
            "平面与高程可独立勾选：勾哪个转哪个，不勾的原值逐位透传。\n"
            "参数来源不限——平面源（四参数/三参数/二维七参数/平面多项式）直接套用；"
            "大地源（七参数链/直接投影）先按源侧投影把顶点反算为经纬度再走参数链。\n"
            "高程含大地水准面格网时，ξ 按源侧经纬度插值；请在下方填源侧投影参数。"
            "坐标轴序：OSGB 为 (东,北)，本软件测量约定为 (北,东)，已自动换算。"))

        # 1. 模型与输出
        g1 = QGroupBox("1. 模型目录与输出")
        gv1 = QGridLayout(g1)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("含 metadata.xml 的模型根目录")
        self.btn_model = QPushButton("浏览…")
        self.btn_model.clicked.connect(self.pick_model)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("留空则自动取 <模型目录>_转换结果")
        self.btn_out = QPushButton("浏览…")
        self.btn_out.clicked.connect(self.pick_out)
        gv1.addWidget(QLabel("模型目录"), 0, 0)
        gv1.addWidget(self.model_edit, 0, 1)
        gv1.addWidget(self.btn_model, 0, 2)
        gv1.addWidget(QLabel("输出目录"), 1, 0)
        gv1.addWidget(self.out_edit, 1, 1)
        gv1.addWidget(self.btn_out, 1, 2)
        self.info_label = QLabel("未扫描")
        self.info_label.setObjectName("muted")
        self.info_label.setWordWrap(True)
        gv1.addWidget(self.info_label, 2, 0, 1, 3)
        v.addWidget(g1)

        # 2. 转换参数
        g2 = QGroupBox("2. 转换参数与模式")
        gv2 = QGridLayout(g2)
        self.param_combo = QComboBox()
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self.reload_params)
        # 平面 / 高程解耦：两个开关独立勾选，与「参数应用转换」页同口径
        self.chk_plane = QCheckBox("平面转换（x=北, y=东）")
        self.chk_plane.setChecked(True)
        self.chk_plane.setToolTip(
            "应用参数的平面部分（四参数/三参数/二维七参数/投影/多项式）。\n"
            "不勾选则 XY 原值透传。")
        self.chk_height = QCheckBox("高程转换（Z）")
        self.chk_height.setChecked(True)
        self.chk_height.setToolTip(
            "应用参数的高程部分：高程拟合 / 大地水准面格网 ξ / 多项式高程。\n"
            "不勾选则 Z 原值透传。\n"
            "带大地水准面格网的参数需源侧投影（projection_src.l0）以便反算经纬度。")
        gv2.addWidget(QLabel("参数"), 0, 0)
        gv2.addWidget(self.param_combo, 0, 1, 1, 2)
        gv2.addWidget(self.btn_reload, 0, 3)
        gv2.addWidget(QLabel("转换内容"), 1, 0)
        ch_box = QHBoxLayout()
        ch_box.addWidget(self.chk_plane)
        ch_box.addWidget(self.chk_height)
        ch_box.addStretch(1)
        gv2.addLayout(ch_box, 1, 1, 1, 3)
        self.param_hint = QLabel("")
        self.param_hint.setObjectName("muted")
        self.param_hint.setWordWrap(True)
        gv2.addWidget(self.param_hint, 3, 0, 1, 4)
        v.addWidget(g2)

        # 3. 投影参数（与其余模块同口径：带号 / 中央子午线 / 假东）
        gproj = QGroupBox("3. 投影参数（平面坐标含带号、或参数未配源侧投影时必填）")
        gvp = QGridLayout(gproj)
        self.proj_detected = QLabel("未识别")
        self.proj_detected.setObjectName("muted")
        self.proj_detected.setWordWrap(True)
        self.chk_use_proj = QCheckBox("启用源侧投影参数")
        self.chk_use_proj.setToolTip(
            "勾选后，下面填写的带号/L0/假东会作为**源侧投影**参与计算：\n"
            "  · 把平面顶点反算为经纬度（大地源参数、大地水准面格网ξ 必需）\n"
            "  · 剥离东坐标里的高斯带号\n"
            "参数自带的 projection_src 优先级低于此处，勾选即覆盖。")
        self.chk_zone = QCheckBox("SRSOrigin 东坐标含带号")
        self.chk_zone.setToolTip(
            "ContextCapture 有时把高斯带号写进东坐标（如 39595251 = 39带 + 595251）。\n"
            "勾选后按带号剥离再反算经纬度；判错会导致反算完全失真，界面会自动识别。")
        self.zone_spin = QSpinBox()
        self.zone_spin.setRange(0, 60)
        self.zone_spin.setToolTip("高斯带号（0 = 不带号）")
        self.l0_spin = QDoubleSpinBox()
        self.l0_spin.setRange(-180.0, 180.0)
        self.l0_spin.setDecimals(8)
        self.l0_spin.setSuffix(" °")
        self.l0_spin.setToolTip("源侧中央子午线 L0（度）。带号可确定时自动填入。")
        self.y0_spin = QDoubleSpinBox()
        self.y0_spin.setRange(-1e7, 1e7)
        self.y0_spin.setDecimals(3)
        self.y0_spin.setValue(500000.0)
        self.y0_spin.setToolTip("源侧假东 y0（国内惯例 500000 m）")
        self.btn_proj_auto = QPushButton("从模型自动识别")
        self.btn_proj_auto.setToolTip("读 metadata.xml 的 SRSOrigin，按东坐标量级判定是否含带号并推算 L0")
        self.btn_proj_auto.clicked.connect(self.do_detect_proj)
        self.chk_zone.toggled.connect(self._on_zone_toggled)
        self.chk_use_proj.toggled.connect(self._on_proj_toggled)
        gvp.addWidget(QLabel("源侧投影"), 0, 0)
        gvp.addWidget(self.btn_proj_auto, 0, 1)
        gvp.addWidget(self.chk_use_proj, 0, 2, 1, 2)
        gvp.addWidget(self.chk_zone, 1, 2, 1, 2)
        gvp.addWidget(QLabel("带号"), 2, 0)
        gvp.addWidget(self.zone_spin, 2, 1)
        gvp.addWidget(QLabel("中央子午线 L0"), 2, 2)
        gvp.addWidget(self.l0_spin, 2, 3)
        gvp.addWidget(QLabel("假东 y0"), 3, 0)
        gvp.addWidget(self.y0_spin, 3, 1)
        gvp.addWidget(self.proj_detected, 4, 0, 1, 4)
        v.addWidget(gproj)

        # 4. 高级选项
        g3 = QGroupBox("4. 高级")
        gv3 = QGridLayout(g3)
        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(1, 64)
        self.threads_spin.setValue(min(16, max(1, os.cpu_count() or 4)))
        self.threads_spin.setToolTip("C++ 工具内部线程数；实测 16 线程在整模型上接近 I/O 上限")
        self.compress_combo = QComboBox()
        self.compress_combo.addItem("压缩（推荐，实测体积 ~0.8–1.7×）",
                                    flash.DEFAULT_OPTIONS)
        self.compress_combo.addItem("仅几何压缩（保留原纹理质量）", "Compressor=zlib")
        self.compress_combo.addItem("不压缩（体积约 3.8×）", "")
        self.bin_edit = QLineEdit("")
        self.bin_edit.setPlaceholderText("osgxform 路径；留空则自动查找（native/osgxform 或 /tmp/osgxform）")
        gv3.addWidget(QLabel("线程数"), 0, 0)
        gv3.addWidget(self.threads_spin, 0, 1)
        gv3.addWidget(QLabel("输出编码"), 0, 2)
        gv3.addWidget(self.compress_combo, 0, 3)
        gv3.addWidget(QLabel("转换器"), 1, 0)
        gv3.addWidget(self.bin_edit, 1, 1, 1, 3)
        v.addWidget(g3)

        # 5. 执行
        g4 = QGroupBox("5. 执行")
        gv4 = QVBoxLayout(g4)
        r4 = QHBoxLayout()
        self.btn_preview = QPushButton("检查")
        self.btn_preview.setToolTip("只读检查：统计瓦片、解析 SRS、试算仿射系数，不写任何文件")
        self.btn_run = QPushButton("开始转换")
        self.btn_run.setToolTip("整模型转换；输出写入指定目录，绝不覆盖输入")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        r4.addWidget(self.btn_preview)
        r4.addWidget(self.btn_run)
        r4.addWidget(self.btn_cancel)
        r4.addStretch(1)
        gv4.addLayout(r4)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)          # 不定长：整批一次调用，无逐瓦片进度
        self.bar.setVisible(False)
        gv4.addWidget(self.bar)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        gv4.addWidget(self.status_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        gv4.addWidget(self.log_view)
        v.addWidget(g4)
        v.addStretch(1)

        self.chk_plane.toggled.connect(self._update_param_hint)
        self.chk_height.toggled.connect(self._update_param_hint)
        self._detected_zone = None
        self.btn_preview.clicked.connect(self.do_check)
        self.btn_run.clicked.connect(self.do_run)
        self.btn_cancel.clicked.connect(self.do_cancel)
        self.reload_params()

    def _mode_label(self) -> str:
        """勾选框 → 人类可读描述（日志/对话框用）。"""
        try:
            m = self.current_mode()
        except ValueError:
            return "（未选择）"
        return {"xyz": "平面 + 高程", "xy": "仅平面", "z": "仅高程"}[m]

    def do_detect_proj(self):
        """从模型 metadata.xml 的 SRSOrigin 自动识别带号与中央子午线。"""
        model = self.model_edit.text().strip()
        if not model:
            _quiet_warn(self, "提示", "请先选择模型目录")
            return
        try:
            from app.osgb.osgb_job import find_model_root
            from app.osgb.srs import load_metadata
            from app.core.projection import detect_gauss_zone
            root = find_model_root(model)
            srs = load_metadata(root / "metadata.xml")
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "读取失败", f"{type(e).__name__}: {e}")
            return
        # OSGB 轴序：第一分量=东，第二分量=北
        e_abs = float(srs.origin[0])
        z = detect_gauss_zone(e_abs)
        self._detected_zone = z
        # L0 优先级：① 参数自带 projection_src.l0 ② 带号公式 ③ 留空让用户填
        l0_par = None
        nm = self.param_combo.currentData()
        if nm:
            try:
                d = ParamLibrary(PARAMS_DIR).load(nm)
                ps = d.get("projection_src") or {}
                if ps.get("l0") is not None:
                    l0_par = float(ps["l0"])
            except Exception:  # noqa: BLE001
                pass
        if z:
            self.chk_use_proj.setChecked(True)
            self.chk_zone.setChecked(True)
            self.zone_spin.setValue(int(z["zone"]))
            l0_use = l0_par if l0_par is not None else float(z["l0"])
            self.l0_spin.setValue(l0_use)
            src = "参数 projection_src" if l0_par is not None else "%s公式" % z["band"]
            self.proj_detected.setText(
                "SRS %s｜东坐标 %.3f 判定**含带号**：%s带号%d，L0=%.8g°（来自%s），"
                "剥离后东=%.3f"
                % (srs.srs, e_abs, z["band"], z["zone"], l0_use, src, z["y_stripped"]))
        else:
            self.chk_zone.setChecked(False)
            self.zone_spin.setValue(0)
            if l0_par is not None:
                self.l0_spin.setValue(l0_par)
                self.proj_detected.setText(
                    "SRS %s｜东坐标 %.3f 不含带号（按 500km 假东判定）；"
                    "L0=%.8g° 取自参数 projection_src" % (srs.srs, e_abs, l0_par))
            else:
                self.proj_detected.setText(
                    "SRS %s｜东坐标 %.3f 不含带号（按 500km 假东判定）；"
                    "参数未提供源侧投影，**请手动填中央子午线 L0**"
                    % (srs.srs, e_abs))
        self._on_zone_toggled()

    def _on_zone_toggled(self):
        # 只做联动使能，不改按钮状态；提示由调用方在需要时刷新，
        # 避免 setChecked() 触发的信号回调里重入 _update_param_hint。
        on = self.chk_use_proj.isChecked()
        self.zone_spin.setEnabled(on and self.chk_zone.isChecked())
        self.l0_spin.setEnabled(on)
        self.y0_spin.setEnabled(on)
        self.btn_proj_auto.setEnabled(True)

    def _on_proj_toggled(self):
        """启用/停用源侧投影参数 → 联动使能并刷新提示。"""
        self._on_zone_toggled()
        self._update_param_hint()

    def proj_override(self) -> dict | None:
        """界面上的源侧投影参数 → 合并进参数文档 projection_src 的覆盖字典。

        以「启用源侧投影参数」勾选为准，**不用数值当哨兵**
        （L0=0 是格林尼治附近的合法值，不能拿它表示"未填"）。
        """
        if not self.chk_use_proj.isChecked():
            return None
        l0 = float(self.l0_spin.value())
        zone = int(self.zone_spin.value()) if self.chk_zone.isChecked() else None
        return {"l0": l0,
                "zone": zone,
                "y0": float(self.y0_spin.value()),
                "x0": 0.0,
                "h0": 0.0,
                "rigorous": False,
                "b0": 25.9}

    def current_mode(self) -> str:
        """勾选框 → 模式键（"xyz" / "xy" / "z"）。两个都不勾选时拒绝。"""
        dp, dh = self.chk_plane.isChecked(), self.chk_height.isChecked()
        if dp and dh:
            return "xyz"
        if dp:
            return "xy"
        if dh:
            return "z"
        raise ValueError("请至少勾选一项转换内容（平面 / 高程）")

    # ---------- 辅助 ----------

    def _find_binary(self) -> str:
        """定位 osgxform：用户指定 > 项目内 native/ > /tmp/。"""
        given = self.bin_edit.text().strip()
        if given:
            return given
        local = _ROOT / "app" / "osgb" / "native" / "osgxform"
        if local.exists():
            return str(local)
        return "/tmp/osgxform"

    def pick_model(self):
        d = QFileDialog.getExistingDirectory(self, "选择 OSGB 模型根目录（含 metadata.xml）")
        if d:
            self.model_edit.setText(d)
            if not self.out_edit.text().strip():
                self.out_edit.setText(str(Path(d).parent / (Path(d).name + "_转换结果")))
            self.do_detect_proj()
            self.do_check()

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_edit.setText(d)

    def reload_params(self):
        self.param_combo.clear()
        try:
            for p in ParamLibrary(PARAMS_DIR).list():
                self.param_combo.addItem(p["name"], p["name"])
        except Exception as e:  # noqa: BLE001
            self.param_hint.setText(f"读取参数库失败：{e}")
            return
        if self.param_combo.count() == 0:
            self.param_hint.setText("参数库为空，请先在「参数库」页保存转换参数。")
        else:
            self.param_combo.currentIndexChanged.connect(self._on_param_changed)
            self._on_param_changed()

    def _on_param_changed(self):
        self._update_param_hint()

    def _update_param_hint(self):
        """报告参数含哪些部分 + 与 OSGB 顶点空间的匹配情况，并决定能否执行。"""
        name = self.param_combo.currentData()
        if not name:
            self.param_hint.setText("参数库为空，请先在「控制点转换」页计算并保存参数。")
            self.btn_run.setEnabled(False)
            return
        try:
            doc = ParamLibrary(PARAMS_DIR).load(name)
        except Exception as e:  # noqa: BLE001
            self.param_hint.setText(f"加载失败：{e}")
            self.btn_run.setEnabled(False)
            return
        kind = doc.get("kind", "?")
        sk = doc.get("source_kind", "?")
        tk = doc.get("target_kind", "?")
        # pages.py:4390 —— 与全项目同口径的"源为大地坐标"判定
        src_lonlat = (sk == "lonlat") or kind in ("seven", "three", "chain74")

        parts = []
        if doc.get("planar4") or doc.get("planar3") or kind in ("planar4", "planar3",
                                                                "seven2d", "poly2d", "poly3d"):
            parts.append("平面")
        if doc.get("heightfit"):
            parts.append("高程拟合(%s)" % doc["heightfit"].get("value_type", "xi"))
        if doc.get("geoid_grid"):
            parts.append("大地水准面格网「%s」" % doc["geoid_grid"])
        if (doc.get("height_flags") or {}).get("poly"):
            parts.append("多项式高程")
        if not parts:
            parts.append("（无明显平面/高程分量）")

        # 源侧投影可用性：界面覆盖 > 参数自带
        has_proj = bool(self.proj_override()) or \
            bool((doc.get("projection_src") or {}).get("l0"))
        msg = "类型 %s｜源 %s → 目标 %s｜含：%s" % (kind, sk, tk, "、".join(parts))
        ok = True
        if src_lonlat:
            if has_proj:
                msg += ("\n源为大地坐标：先把 OSGB 平面顶点按源侧投影反算为经纬度，"
                        "再走参数的 %s 链（经纬度 → 目标平面）。平面与高程可独立勾选。" % kind)
            else:
                ok = False
                msg += ("\n⚠ 源为大地坐标（%s），需要把平面顶点反算为经纬度，"
                        "但缺源侧投影——请在下方「投影参数」填中央子午线 L0"
                        "（模型东坐标含带号时勾选带号）。" % kind)
        if doc.get("geoid_grid") and not has_proj:
            ok = False
            msg += ("\n⚠ 含大地水准面格网：ξ 需要源侧经纬度，必须提供源侧投影 L0。")
        if (doc.get("heightfit") or {}).get("space") == "lonlat" and not has_proj:
            msg += "\n提示：高程拟合在经纬度空间，缺 L0 时该部分会被跳过。"
        self.param_hint.setText(msg)
        self.btn_run.setEnabled(ok)

    def _log(self, msg: str):
        self.log_view.append(msg)

    # ---------- 检查 ----------

    def do_check(self):
        model = self.model_edit.text().strip()
        if not model:
            _quiet_warn(self, "提示", "请先选择模型目录")
            return
        try:
            from app.osgb.osgb_job import find_model_root, iter_tile_files
            from app.osgb import flash as _f
            from app.osgb.srs import load_metadata
            root = find_model_root(model)
            srs = load_metadata(root / "metadata.xml")
            tiles = iter_tile_files(root)
            size_mb = sum(t.stat().st_size for t in tiles) / 1048576
            lines = [f"模型根：{root}",
                     f"瓦片：{len(tiles)} 个，共 {size_mb:.1f} MB",
                     f"SRS：{srs.describe()}",
                     f"原点：({srs.origin[0]:.4f}, {srs.origin[1]:.4f}, {srs.origin[2]:.4f})"]
            name = self.param_combo.currentData()
            if name:
                doc = ParamLibrary(PARAMS_DIR).load(name)
                mode = self.current_mode()
                A, b, new = _f.coeffs_for_document(doc, srs, mode=mode)
                lines.append("")
                lines.append(f"仿射系数（{mode} 模式）：")
                for i in range(3):
                    lines.append("  A[%d] = [%s]" % (i, ", ".join("%.9f" % A[i][j] for j in range(3))))
                lines.append("  b    = [%s]" % ", ".join("%.4f" % v for v in b))
                lines.append("")
                lines.append("X 位移 %.3f m，Y 位移 %.3f m，Z 位移 %.3f m" % (b[0], b[1], b[2]))
                shift = math.hypot(b[0], b[1])
                if shift > 50000:
                    lines.append("⚠ 平面位移超过 50 km——请确认参数与模型是同一坐标系，"
                                 "选错参数会静默产生巨大偏移")
            txt = "\n".join(lines)
            self.info_label.setText(txt)
            self.status_label.setText("检查完成（未写任何文件）")
        except Exception as e:  # noqa: BLE001
            self.info_label.setText("")
            _quiet_warn(self, "检查失败", f"{type(e).__name__}: {e}")

    # ---------- 执行 ----------

    def do_run(self):
        model = self.model_edit.text().strip()
        out = self.out_edit.text().strip()
        name = self.param_combo.currentData()
        if not model or not name:
            _quiet_warn(self, "提示", "请先选择模型目录与转换参数")
            return
        if not out:
            out = str(Path(model).parent / (Path(model).name + "_转换结果"))
            self.out_edit.setText(out)
        try:
            if Path(out).resolve() == Path(model).resolve():
                _quiet_warn(self, "拒绝执行", "输出目录不能与模型目录相同——本项目禁止覆盖输入。")
                return
        except OSError:
            pass
        try:
            doc = ParamLibrary(PARAMS_DIR).load(name)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "参数加载失败", str(e))
            return
        if doc.get("source_kind") != "planar":
            _quiet_warn(self, "参数不适用",
                        f"该参数的源坐标系是 {doc.get('source_kind')}，不是平面坐标。\n"
                        "OSGB 顶点是相对 SRSOrigin 的平面偏移量，只能用平面源参数转换。")
            return

        # 二次确认（不可逆的批量写盘）
        ans = QMessageBox.question(
            self, "确认转换",
            f"模型：{model}\n输出：{out}\n参数：{name}\n"
            f"转换内容：{self._mode_label()}\n\n"
            f"将写入 {self.threads_spin.value()} 线程转换后的完整模型。是否继续？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        self._start(model, out, doc)

    def _start(self, model, out, doc):
        from app.osgb.osgb_worker import OsgbConvertWorker
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_preview.setEnabled(False)
        self.bar.setVisible(True)
        self.log_view.clear()
        self._log(f"模型：{model}")
        self._log(f"输出：{out}")
        self._log(f"参数：{self.param_combo.currentText()}｜转换内容：{self._mode_label()}")
        self._log(f"转换器：{self._find_binary()}")
        self.status_label.setText("正在扫描并转换…")
        self._worker = OsgbConvertWorker(
            model, out, doc,
            mode=self.current_mode(),
            binary=self._find_binary(),
            options=self.compress_combo.currentData(),
            threads=self.threads_spin.value(),
            keep_origin=True,
            proj_override=self.proj_override())
        self._worker.tick.connect(self._log)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def do_cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")

    def _reset_buttons(self):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_preview.setEnabled(True)
        self.bar.setVisible(False)

    def _on_done(self, res):
        self._reset_buttons()
        self._result = res
        self._log("")
        self._log(res.summary())
        if res.raw:
            self._log(res.raw.strip()[:1500])
        self.status_label.setText("完成：" + res.summary())
        _oplog().log("osgb_convert", {
            "model": self.model_edit.text().strip(),
            "out": self.out_edit.text().strip(),
            "param": self.param_combo.currentText(),
            "mode": self.current_mode(),
            "ok": res.ok, "failed": res.failed, "vertices": res.vertices,
            "seconds": round(res.seconds, 2)})
        _quiet_info(self, "转换完成", res.summary())

    def _on_fail(self, msg):
        self._reset_buttons()
        self.status_label.setText("失败：" + msg)
        self._log("失败：" + msg)
        _quiet_warn(self, "转换失败", msg)



class LasPage(QWidget):
    """点云模型转换页（LAS）。

    契约（与 OSGB 页一致）：
      · 只改 X/Y/Z 坐标，**非坐标数据一律不动**（强度/回波/分类/GPS 时间/RGB 等）
      · scale/offset 保持源文件的值（它们决定量化精度，不属于坐标转换）
      · 平面与高程独立勾选；换算委托 app.logic.apply_params
      · 轴序：LAS 惯例 X=东、Y=北；本项目测量约定 x=北、y=东
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._files: list = []
        v = QVBoxLayout(self)
        v.addWidget(_title(
            "点云模型坐标转换（LAS）",
            "对 LAS/LAZ 点云逐点转换 X/Y/Z 坐标。**只动坐标，其余属性（强度、回波、分类、"
            "GPS 时间、RGB、扫描角、点源 ID）与头部元数据（点格式、scale/offset、VLR/CRS）"
            "一律原样保留**。\n"
            "平面与高程可独立勾选；轴序 LAS 为 (东,北)、本软件为 (北,东)，已自动换算。"
            "LAS 的 scale 是量化步长（常见 0.0001 m），保持不变。"))

        # 1. 数据与输出
        g1 = QGroupBox("1. 点云目录与输出")
        gv1 = QGridLayout(g1)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("含 .las/.laz 的目录（可递归）")
        self.btn_src = QPushButton("浏览…")
        self.btn_src.clicked.connect(self.pick_src)
        self.dst_edit = QLineEdit()
        self.dst_edit.setPlaceholderText("留空则自动取 <源目录>_转换结果")
        self.btn_dst = QPushButton("浏览…")
        self.btn_dst.clicked.connect(self.pick_dst)
        self.chk_recursive = QCheckBox("递归子目录")
        self.chk_recursive.setChecked(True)
        gv1.addWidget(QLabel("点云目录"), 0, 0)
        gv1.addWidget(self.src_edit, 0, 1)
        gv1.addWidget(self.btn_src, 0, 2)
        gv1.addWidget(QLabel("输出目录"), 1, 0)
        gv1.addWidget(self.dst_edit, 1, 1)
        gv1.addWidget(self.btn_dst, 1, 2)
        gv1.addWidget(self.chk_recursive, 2, 1)
        self.info_label = QLabel("未扫描")
        self.info_label.setObjectName("muted")
        self.info_label.setWordWrap(True)
        gv1.addWidget(self.info_label, 3, 0, 1, 3)
        v.addWidget(g1)

        # 2. 参数与转换内容
        g2 = QGroupBox("2. 转换参数与内容")
        gv2 = QGridLayout(g2)
        self.param_combo = QComboBox()
        self.btn_reload = QPushButton("刷新")
        self.btn_reload.clicked.connect(self.reload_params)
        self.chk_plane = QCheckBox("平面转换（x=北, y=东）")
        self.chk_plane.setChecked(True)
        self.chk_height = QCheckBox("高程转换（Z）")
        self.chk_height.setChecked(True)
        gv2.addWidget(QLabel("参数"), 0, 0)
        gv2.addWidget(self.param_combo, 0, 1, 1, 2)
        gv2.addWidget(self.btn_reload, 0, 3)
        gv2.addWidget(QLabel("转换内容"), 1, 0)
        ch_box = QHBoxLayout()
        ch_box.addWidget(self.chk_plane)
        ch_box.addWidget(self.chk_height)
        ch_box.addStretch(1)
        gv2.addLayout(ch_box, 1, 1, 1, 3)
        self.param_hint = QLabel("")
        self.param_hint.setObjectName("muted")
        self.param_hint.setWordWrap(True)
        gv2.addWidget(self.param_hint, 2, 0, 1, 4)
        v.addWidget(g2)

        # 3. 源侧投影（大地源参数 / 带号剥离需要）
        g3 = QGroupBox("3. 源侧投影参数（参数源为大地坐标、或平面坐标含带号时必填）")
        gv3 = QGridLayout(g3)
        self.chk_use_proj = QCheckBox("启用源侧投影参数")
        self.zone_spin = QSpinBox()
        self.zone_spin.setRange(0, 60)
        self.l0_spin = QDoubleSpinBox()
        self.l0_spin.setRange(-180.0, 180.0)
        self.l0_spin.setDecimals(8)
        self.l0_spin.setSuffix(" °")
        self.y0_spin = QDoubleSpinBox()
        self.y0_spin.setRange(-1e7, 1e7)
        self.y0_spin.setDecimals(3)
        self.y0_spin.setValue(500000.0)
        self.chk_zone = QCheckBox("X（东）含带号")
        self.btn_proj_auto = QPushButton("从 LAS 头自动识别")
        self.btn_proj_auto.clicked.connect(self.do_detect_proj)
        gv3.addWidget(self.btn_proj_auto, 0, 0)
        gv3.addWidget(self.chk_use_proj, 0, 1, 1, 2)
        gv3.addWidget(QLabel("带号"), 1, 0)
        gv3.addWidget(self.zone_spin, 1, 1)
        gv3.addWidget(self.chk_zone, 1, 2)
        gv3.addWidget(QLabel("中央子午线 L0"), 2, 0)
        gv3.addWidget(self.l0_spin, 2, 1)
        gv3.addWidget(QLabel("假东 y0"), 2, 2)
        gv3.addWidget(self.y0_spin, 2, 3)
        self.proj_detected = QLabel("未识别")
        self.proj_detected.setObjectName("muted")
        self.proj_detected.setWordWrap(True)
        gv3.addWidget(self.proj_detected, 3, 0, 1, 4)
        v.addWidget(g3)

        # 4. 执行
        g4 = QGroupBox("4. 执行")
        gv4 = QVBoxLayout(g4)
        r4 = QHBoxLayout()
        self.btn_scan = QPushButton("扫描")
        self.btn_scan.clicked.connect(self.do_scan)
        self.btn_run = QPushButton("开始转换")
        self.btn_run.clicked.connect(self.do_run)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.do_cancel)
        r4.addWidget(self.btn_scan)
        r4.addWidget(self.btn_run)
        r4.addWidget(self.btn_cancel)
        r4.addStretch(1)
        gv4.addLayout(r4)
        self.bar = QProgressBar()
        gv4.addWidget(self.bar)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        gv4.addWidget(self.status_label)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(170)
        gv4.addWidget(self.log_view)
        v.addWidget(g4)
        v.addStretch(1)

        self.chk_use_proj.toggled.connect(self._on_proj_toggled)
        self.chk_zone.toggled.connect(self._on_proj_toggled)
        self.chk_plane.toggled.connect(self._update_param_hint)
        self.chk_height.toggled.connect(self._update_param_hint)
        self.reload_params()

    # ---------- 辅助 ----------

    def current_mode(self) -> str:
        """勾选框 → 模式键（xyz / xy / z）。"""
        dp, dh = self.chk_plane.isChecked(), self.chk_height.isChecked()
        if dp and dh:
            return "xyz"
        if dp:
            return "xy"
        if dh:
            return "z"
        raise ValueError("请至少勾选一项转换内容（平面 / 高程）")

    def _mode_label(self) -> str:
        try:
            m = self.current_mode()
        except ValueError:
            return "（未选择）"
        return {"xyz": "平面 + 高程", "xy": "仅平面", "z": "仅高程"}[m]

    def pick_src(self):
        d = QFileDialog.getExistingDirectory(self, "选择点云目录")
        if d:
            self.src_edit.setText(d)
            if not self.dst_edit.text().strip():
                self.dst_edit.setText(str(Path(d).parent / (Path(d).name + "_转换结果")))
            self.do_scan()
            self.do_detect_proj()

    def pick_dst(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.dst_edit.setText(d)

    def reload_params(self):
        self.param_combo.clear()
        try:
            for p in ParamLibrary(PARAMS_DIR).list():
                self.param_combo.addItem(p["name"], p["name"])
        except Exception as e:  # noqa: BLE001
            self.param_hint.setText("读取参数库失败：%s" % e)
            return
        try:
            self.param_combo.currentIndexChanged.disconnect()
        except Exception:  # noqa: BLE001
            pass
        self.param_combo.currentIndexChanged.connect(self._update_param_hint)
        self._update_param_hint()

    def _update_param_hint(self):
        from app.pointcloud.las_io import has_laspy
        if not has_laspy():
            self.param_hint.setText(
                "缺少 laspy，无法读写 LAS。请安装：pip install laspy")
            self.btn_run.setEnabled(False)
            return
        name = self.param_combo.currentData()
        if not name:
            self.param_hint.setText("参数库为空，请先在「控制点转换」页计算并保存参数。")
            self.btn_run.setEnabled(False)
            return
        try:
            doc = ParamLibrary(PARAMS_DIR).load(name)
        except Exception as e:  # noqa: BLE001
            self.param_hint.setText("加载失败：%s" % e)
            self.btn_run.setEnabled(False)
            return
        kind = doc.get("kind", "?")
        sk = doc.get("source_kind", "?")
        tk = doc.get("target_kind", "?")
        src_lonlat = (sk == "lonlat") or kind in ("seven", "three", "chain74")
        parts = []
        if doc.get("planar4") or doc.get("planar3") or kind in (
                "planar4", "planar3", "seven2d", "poly2d", "poly3d"):
            parts.append("平面")
        if doc.get("heightfit"):
            parts.append("高程拟合(%s)" % doc["heightfit"].get("value_type", "xi"))
        if doc.get("geoid_grid"):
            parts.append("大地水准面格网「%s」" % doc["geoid_grid"])
        if not parts:
            parts.append("（无明显平面/高程分量）")
        has_proj = bool(self.proj_override()) or bool(
            (doc.get("projection_src") or {}).get("l0"))
        msg = "类型 %s | 源 %s → 目标 %s | 含：%s" % (kind, sk, tk, "、".join(parts))
        ok = True
        if src_lonlat and not has_proj:
            ok = False
            msg += ("\n⚠ 源为大地坐标（%s），需要把点云平面坐标反算为经纬度，"
                    "但缺源侧投影——请勾选「启用源侧投影参数」并填中央子午线 L0。" % kind)
        if doc.get("geoid_grid") and not has_proj:
            ok = False
            msg += "\n⚠ 含大地水准面格网：ξ 需要源侧经纬度，必须提供源侧投影 L0。"
        self.param_hint.setText(msg)
        self.btn_run.setEnabled(ok)

    def _on_proj_toggled(self):
        on = self.chk_use_proj.isChecked()
        self.zone_spin.setEnabled(on)
        self.chk_zone.setEnabled(on)
        self.l0_spin.setEnabled(on)
        self.y0_spin.setEnabled(on)
        self._update_param_hint()

    def proj_override(self) -> dict | None:
        if not self.chk_use_proj.isChecked():
            return None
        zone = int(self.zone_spin.value()) if self.chk_zone.isChecked() else None
        return {"l0": float(self.l0_spin.value()), "zone": zone,
                "y0": float(self.y0_spin.value()), "x0": 0.0, "h0": 0.0,
                "rigorous": False, "b0": 25.9}

    def _log(self, msg: str):
        self.log_view.append(msg)

    # ---------- 扫描 / 识别 ----------

    def do_scan(self):
        src = self.src_edit.text().strip()
        if not src:
            _quiet_warn(self, "提示", "请先选择点云目录")
            return
        try:
            from app.pointcloud import las_job
            self._files = las_job.find_las_files(src, recursive=self.chk_recursive.isChecked())
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "扫描失败", "%s: %s" % (type(e).__name__, e))
            return
        if not self._files:
            self.info_label.setText("未找到 .las/.laz 文件")
            return
        total = sum(f.stat().st_size for f in self._files)
        lines = ["共 %d 个文件，%.2f GB" % (len(self._files), total / 1073741824.0)]
        try:
            from app.pointcloud import las_io
            info = las_io.read_info(self._files[0])
            lines.append("")
            lines.append("首个文件头信息：")
            lines.append(info.describe())
        except Exception as e:  # noqa: BLE001
            lines.append("读取头信息失败：%s" % e)
        self.info_label.setText("\n".join(lines))
        self.status_label.setText("扫描完成：%d 个文件" % len(self._files))

    def do_detect_proj(self):
        """从 LAS 头读 CRS 与坐标量级，推测带号与中央子午线。"""
        src = self.src_edit.text().strip()
        if not src:
            _quiet_warn(self, "提示", "请先选择点云目录")
            return
        try:
            from app.pointcloud import las_job, las_io
            from app.core.projection import detect_gauss_zone
            files = self._files or las_job.find_las_files(
                src, recursive=self.chk_recursive.isChecked())
            if not files:
                _quiet_warn(self, "提示", "未找到 LAS 文件")
                return
            info = las_io.read_info(files[0])
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "读取失败", "%s: %s" % (type(e).__name__, e))
            return
        x_lo, x_hi = float(info.mins[0]), float(info.maxs[0])
        z = detect_gauss_zone(x_hi)
        self.chk_use_proj.setChecked(True)
        if z:
            self.chk_zone.setChecked(True)
            self.zone_spin.setValue(int(z["zone"]))
            self.l0_spin.setValue(float(z["l0"]))
            self.proj_detected.setText(
                "X（东）最大值 %.3f 判定为含带号：%s带号%d，L0 自动锁定 %.8g°"
                % (x_hi, z["band"], z["zone"], z["l0"]))
        else:
            self.chk_zone.setChecked(False)
            # LAS 头的 CRS(WKT) 里通常直接带 central_meridian，优先采用
            l0_crs = self._l0_from_crs(info.crs_wkt)
            if l0_crs is not None:
                self.l0_spin.setValue(l0_crs)
                self.proj_detected.setText(
                    "X（东）范围 %.3f~%.3f 不含带号；CRS 中央子午线 = %.8g°，已自动填入"
                    % (x_lo, x_hi, l0_crs))
            else:
                self.proj_detected.setText(
                    "X（东）范围 %.3f~%.3f 不含带号；CRS %s。请按模型的中央子午线填 L0"
                    % (x_lo, x_hi, "已定义但未含中央子午线" if info.crs_wkt
                       else "未定义"))
        self._on_proj_toggled()

    @staticmethod
    def _l0_from_crs(wkt: str):
        """从 LAS 头的 CRS WKT 里取 central_meridian（度）。取不到返回 None。

        只做"读元数据"，不推测：LAS 的 WKT 是数据自带的权威信息。
        """
        if not wkt:
            return None
        # 正规做法：交给 pyproj（公开库）解析，WKT1/WKT2/PROJ 串都认。
        # 曾经用正则匹配 PARAMETER["central_meridian",...]，只对 WKT1 有效，
        # 而这批 LAS 存的是 WKT2（CONVERSION[...PARAMETER["Central meridian",...]]）。
        try:
            from pyproj import CRS
            c = CRS.from_wkt(wkt)
            d = c.to_dict()
            # pyproj 的键是 lon_0（Proj 口径），不是 WKT 里的 central_meridian
            for key in ("lon_0", "central_meridian"):
                v = d.get(key)
                if v is not None:
                    return float(v)
        except Exception:  # noqa: BLE001
            pass
        # 退回文本扫描（覆盖 WKT1/WKT2 两种参数写法）
        try:
            import re
            m = re.search(r'"central[_ ]meridian"\s*,\s*'
                          r'(-?[0-9.]+(?:[eE][-+]?\d+)?)', wkt, re.I)
            return float(m.group(1)) if m else None
        except Exception:  # noqa: BLE001
            return None

    # ---------- 执行 ----------

    def do_run(self):
        src = self.src_edit.text().strip()
        dst = self.dst_edit.text().strip()
        name = self.param_combo.currentData()
        if not src or not name:
            _quiet_warn(self, "提示", "请先选择点云目录与转换参数")
            return
        if not dst:
            dst = str(Path(src).parent / (Path(src).name + "_转换结果"))
            self.dst_edit.setText(dst)
        try:
            if Path(dst).resolve() == Path(src).resolve():
                _quiet_warn(self, "拒绝执行",
                            "输出目录不能与输入目录相同——本项目禁止覆盖源数据。")
                return
        except OSError:
            pass
        try:
            mode = self.current_mode()
        except ValueError as e:
            _quiet_warn(self, "提示", str(e))
            return
        try:
            doc = ParamLibrary(PARAMS_DIR).load(name)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "参数加载失败", str(e))
            return
        ans = QMessageBox.question(
            self, "确认转换",
            "点云目录：%s\n输出目录：%s\n参数：%s\n转换内容：%s\n\n"
            "只改 X/Y/Z 坐标，其余属性与头部元数据原样保留。是否继续？"
            % (src, dst, name, self._mode_label()),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ans != QMessageBox.Yes:
            return
        self._start(src, dst, doc, mode)

    def _start(self, src, dst, doc, mode):
        from app.pointcloud.las_worker import LasConvertWorker
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_scan.setEnabled(False)
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.log_view.clear()
        self._log("点云目录：%s" % src)
        self._log("输出目录：%s" % dst)
        self._log("参数：%s | 转换内容：%s" % (self.param_combo.currentText(),
                                            self._mode_label()))
        self._worker = LasConvertWorker(
            src, dst, doc, mode=mode, proj_override=self.proj_override())
        self._worker.file_progress.connect(self._on_file)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_file(self, idx, total, name):
        self.bar.setValue(int(100.0 * idx / max(1, total)))
        self.status_label.setText("(%d/%d) %s" % (idx + 1, total, name))

    def do_cancel(self):
        if self._worker is not None:
            self._worker.cancel()
            self.status_label.setText("正在取消…")

    def _reset_buttons(self):
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_scan.setEnabled(True)
        self.bar.setValue(100)

    def _on_done(self, res):
        self._reset_buttons()
        self._log("")
        self._log(res.summary())
        for f in res.files:
            if not f.ok:
                self._log("  失败 %s: %s" % (Path(f.src).name, f.reason))
        self.status_label.setText("完成：" + res.summary())
        _oplog().log("las_convert", {
            "src": self.src_edit.text().strip(),
            "dst": self.dst_edit.text().strip(),
            "param": self.param_combo.currentText(),
            "mode": self.current_mode(),
            "files_ok": res.ok_count, "files_fail": res.fail_count,
            "points": res.point_total, "seconds": round(res.seconds, 2)})
        _quiet_info(self, "转换完成", res.summary())

    def _on_fail(self, msg):
        self._reset_buttons()
        self.status_label.setText("失败：" + msg)
        self._log("失败：" + msg)
        _quiet_warn(self, "转换失败", msg)



class PolyPage(QWidget):
    """复杂坐标系转换（二维/三维多项式模型）。

    计算（拟合/求值/残差/量程判别）全部在 app.logic.polynomial；
    参数应用在 app.common.params.ParamApplier 的 poly2d/poly3d 分支。
    本页只做取数、展示与保存。POLY_DISCLAIMER 是该功能的使用前提，
    必须同时保留在页面上和保存出的参数 JSON 里。
    """

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        warn = QLabel(POLY_DISCLAIMER)
        warn.setWordWrap(True)
        warn.setStyleSheet(f"color:{WARN}; border:1px solid {WARN}; border-radius:4px; "
                           "padding:6px; background:transparent;")
        v.addWidget(warn)

        top = QGroupBox("模型与点对")
        g = QGridLayout(top)
        # 模型二选一：三维多项式绝大多数场景用不到，默认灰（config.json enable_poly3d 解锁）
        _cfg = _load_app_config()
        self.rb_model2 = QRadioButton("二维多项式")
        self.rb_model3 = QRadioButton("三维多项式")
        self.rb_model2.setChecked(True)
        self.rb_model3.setEnabled(bool(_cfg.get("enable_poly3d")))
        self.rb_model3.setToolTip("" if _cfg.get("enable_poly3d") else
                                  "三维多项式极少使用，默认锁定；在软件目录 config.json 写"
                                  ' {"enable_poly3d": true} 后重启解锁')
        self.rb_model2.toggled.connect(self._sync_poly_grey)
        self.degree_spin = QSpinBox()
        self.degree_spin.setRange(1, 3)
        self.degree_spin.setValue(2)
        g.addWidget(QLabel("模型"), 0, 0)
        g.addWidget(self.rb_model2, 0, 1)
        g.addWidget(self.rb_model3, 0, 2)
        g.addWidget(QLabel("阶数"), 0, 3)
        g.addWidget(self.degree_spin, 0, 4)
        g.addWidget(QLabel("（南方口径为固定 2 阶；1/3 阶为本软件扩展，点数相应增减）"), 0, 5)
        btns = QHBoxLayout()
        self.btn_load = QPushButton("导入点对文件…")
        self.btn_add = QPushButton("加行")
        self.btn_del = QPushButton("删行")
        self.btn_clear = QPushButton("清空")
        for b in (self.btn_load, self.btn_add, self.btn_del, self.btn_clear):
            btns.addWidget(b)
        btns.addStretch(1)
        g.addLayout(btns, 1, 0, 1, 5)
        v.addWidget(top)

        # 源/目标侧坐标系（椭球 + 投影）：普通平面坐标一侧需填投影（反算大地参与拟合）；
        # 大地/空间直角/含带号平面一侧自动识别并灰显（带号 L0 自动锁定）
        def _cs_group(title):
            gb = QGroupBox(title)
            gg = QGridLayout(gb)
            ell = QComboBox()
            from app.core.ellipsoid import all_ellipsoids as _ALL
            for nm in _ALL():
                ell.addItem(nm)
            ell.setCurrentText("CGCS2000")
            l0 = QLineEdit("105.00000000"); y0 = QLineEdit("500.00000000")
            x0 = QLineEdit("0.00000000"); h0 = QLineEdit("0.00000000")
            for _w in (l0, y0, x0, h0):
                _w.setMaximumWidth(150)
            rig = QRadioButton("严密"); app_ = QRadioButton("近似")
            app_.setChecked(True)
            b0 = QLineEdit("")
            b0.setPlaceholderText("留空=自动取参与点平均纬度")
            b0.setToolTip("严密工程椭球的平均纬度 B0（Ra(B0) 用）；仅严密模式参与计算，留空自动")
            zl = QLabel(""); zl.setObjectName("muted"); zl.setWordWrap(True)
            for i, (t, w_) in enumerate([("椭球", ell), ("中央子午线(d.ms)", l0),
                                         ("y0加常数(km)", y0), ("x0(km)", x0),
                                         ("投影面大地高(m)", h0), ("平均纬度 B0(d.ms)", b0)]):
                gg.addWidget(QLabel(t), i, 0)
                gg.addWidget(w_, i, 1)
            gg.addWidget(rig, 6, 0)
            gg.addWidget(app_, 6, 1)
            gg.addWidget(zl, 7, 0, 1, 2)
            return {"box": gb, "ell": ell, "l0": l0, "y0": y0, "x0": x0, "h0": h0,
                    "b0": b0, "rig": rig, "app": app_, "zone": zl}
        self.src_cs = _cs_group("源侧坐标系（源为普通平面时填写，用于反算大地）")
        self.dst_cs = _cs_group("目标侧坐标系（目标为普通平面时填写，模型输出大地后正算）")
        cs_row = QHBoxLayout()
        cs_row.addWidget(self.src_cs["box"])
        cs_row.addWidget(self.dst_cs["box"])
        for cs in (self.src_cs, self.dst_cs):
            cs["box"].setMinimumWidth(330)   # 窗口缩窄时表单区不挤压，只缩下方表格
        v.addLayout(cs_row)

        hg = QGroupBox("高程处理（曲面拟合与大地水准面可任意叠加；不计算＝高程透传）")
        hv = QHBoxLayout(hg)
        # 由多项式第三分量：仅三维模型可用（默认灰）；曲面/格网任意叠加；不计算清除全部勾选
        self.chk_h_poly = QCheckBox("由多项式第三分量")
        self.chk_h_surf = QCheckBox("曲面拟合")
        self.chk_h_geoid = QCheckBox("大地水准面模型")
        self.chk_h_none = QCheckBox("不计算")
        self.chk_h_none.setChecked(True)
        self.chk_h_poly.setEnabled(False)   # 默认灰：选三维多项式（config 解锁）后可用
        self.geoid_combo = QComboBox()
        self.geoid_combo.setEnabled(False)
        self.geoid_combo.setMaximumWidth(200)
        for w_ in (self.chk_h_poly, self.chk_h_surf, self.chk_h_geoid, self.chk_h_none):
            hv.addWidget(w_)
        hv.addWidget(QLabel("格网模型"))
        hv.addWidget(self.geoid_combo)
        hv.addStretch(1)
        self.chk_h_poly.setToolTip("高程由三维多项式的第三分量给出（需先解锁并选择三维多项式）")
        self.chk_h_surf.setToolTip("拟合 高程差 dh = f(源坐标) 二次曲面趋势（可与大地水准面叠加：先换算 ξ 再拟合剩余趋势）")
        self.chk_h_geoid.setToolTip("先做 ξ 格网换算：基准高 = 源大地高 − ξ(源经纬度)；可与曲面拟合叠加")
        self.chk_h_none.setToolTip("高程不参与计算，原值透传")
        v.addWidget(hg)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(_MODEL_HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(180)
        v.addWidget(self.table, 1)

        save_row = QHBoxLayout()
        save_row.addWidget(QLabel("参数名"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("保存后可在“参数应用转换”页选用")
        save_row.addWidget(self.name_edit, 1)
        self.btn_fit = QPushButton("拟合并保存到参数库")
        save_row.addWidget(self.btn_fit)
        v.addLayout(save_row)

        self.res_table = QTableWidget(0, 5)
        self.res_table.setHorizontalHeaderLabels(["点号", "Δx(m)", "Δy(m)", "Δh(m)", "点位残差(m)"])
        self.res_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.res_table.setMinimumHeight(140)
        v.addWidget(self.res_table, 1)

        self.summary = QLabel("（未拟合）")
        self.summary.setStyleSheet("color:#6b7a83; background:transparent;")
        v.addWidget(self.summary)

        self.btn_load.clicked.connect(self.import_pairs)
        self.btn_add.clicked.connect(lambda: self.table.insertRow(self.table.rowCount()))
        self.btn_del.clicked.connect(self._del_rows)
        self.btn_clear.clicked.connect(self._clear)
        self.btn_fit.clicked.connect(self.fit_and_save)
        for cb in (self.chk_h_poly, self.chk_h_surf, self.chk_h_geoid, self.chk_h_none):
            cb.toggled.connect(self._on_h_flag)
        self.rb_model2.toggled.connect(self._sync_poly_grey)
        self._model = None
        self._zone_s = None   # 源列带号识别（dict | None）
        self._zone_t = None
        self._src_kind = "planar"   # 源/目标列坐标类型（导入后检测）
        self._dst_kind = "planar"
        self.setMinimumWidth(1010)   # 页面最小宽：再窄只缩表格列，表单区不变形
        self.reload_models()
        self._sync_poly_grey()

    def reload_models(self):
        """填充大地水准面格网模型下拉（models/ 可用模型）。"""
        cur = self.geoid_combo.currentText()
        self.geoid_combo.blockSignals(True)
        self.geoid_combo.clear()
        from app.core.geoid import available_models
        for m in available_models(MODELS_DIR):
            if m["usable"]:
                self.geoid_combo.addItem(m["name"])
        if cur:
            idx = self.geoid_combo.findText(cur)
            if idx >= 0:
                self.geoid_combo.setCurrentIndex(idx)
        self.geoid_combo.blockSignals(False)

    def _poly_proj_values(self, which):
        """读取源/目标侧投影参数 dict（l0 度 / x0_m / y0_m / h0 / rigorous）；L0 留空 → l0=None。"""
        cs = self.src_cs if which == "src" else self.dst_cs
        from app.core import anglefmt
        t = cs["l0"].text().strip()
        try:
            l0 = math.degrees(anglefmt.parse_flexible(t, "dms")) if t else None
        except ValueError as e:
            raise ValueError(f"{'源' if which == 'src' else '目标'}侧中央子午线有误：{e}") from None
        zone = self._zone_s if which == "src" else self._zone_t
        if zone:
            l0 = float(zone["l0"])
        bt = cs["b0"].text().strip()
        try:
            b0_manual = math.degrees(anglefmt.parse_flexible(bt, "dms")) if bt else None
        except ValueError as e:
            raise ValueError(f"{'源' if which == 'src' else '目标'}侧平均纬度有误：{e}") from None
        return {"l0": l0, "b0_manual": b0_manual,
                "x0_m": (float(cs["x0"].text()) * 1000.0 if cs["x0"].text().strip() else 0.0),
                "y0_m": (float(cs["y0"].text()) * 1000.0 if cs["y0"].text().strip() else 500000.0),
                "h0": (float(cs["h0"].text()) if cs["h0"].text().strip() else 0.0),
                "rigorous": cs["rig"].isChecked(),
                "ell": None,   # 椭球对象在保存时按名字填（JSON 安全由 _projection 序列化负责）
                "zone": int(zone["zone"]) if zone else None,
                "band": zone["band"] if zone else None}

    def _on_h_flag(self):
        """高程勾选联动：不计算→清除其余；曲面/格网任意叠加；多项式第三分量与其余互斥。"""
        cb = self.sender()
        if cb is self.chk_h_none and self.chk_h_none.isChecked():
            for w_ in (self.chk_h_poly, self.chk_h_surf, self.chk_h_geoid):
                w_.blockSignals(True)
                w_.setChecked(False)
                w_.blockSignals(False)
        elif cb is not self.chk_h_none and cb.isChecked():
            self.chk_h_none.blockSignals(True)
            self.chk_h_none.setChecked(False)
            self.chk_h_none.blockSignals(False)
            if cb is self.chk_h_poly:   # 多项式第三分量与其余高程来源互斥
                for w_ in (self.chk_h_surf, self.chk_h_geoid):
                    w_.blockSignals(True)
                    w_.setChecked(False)
                    w_.blockSignals(False)
        self._sync_poly_grey()

    def _sync_poly_grey(self, *_):
        """智能灰显：源/目标为大地（或含带号平面）时对应侧投影不起作用并变灰；
        普通平面一侧投影可填（反算大地参与拟合）。联动表头与格网下拉。"""
        from app.core.autoselect import detect_coord_kind
        from app.core.projection import detect_zone_on_column
        s_vals, d_vals = [], []
        for r in range(self.table.rowCount()):
            def cell(c):
                it = self.table.item(r, c)
                try:
                    return float(it.text()) if it and it.text().strip() else None
                except ValueError:
                    return None
            if cell(1) is not None and cell(2) is not None:
                s_vals.append([cell(1), cell(2)])
            if cell(4) is not None and cell(5) is not None:
                d_vals.append([cell(4), cell(5)])
        import numpy as _np
        self._src_kind = (detect_coord_kind(_np.asarray(s_vals))
                          if len(s_vals) >= 2 else "planar")
        self._dst_kind = (detect_coord_kind(_np.asarray(d_vals))
                          if len(d_vals) >= 2 else "planar")
        self._zone_s = (detect_zone_on_column(_np.asarray(s_vals)[:, 1])
                        if self._src_kind == "planar" and s_vals else None)
        self._zone_t = (detect_zone_on_column(_np.asarray(d_vals)[:, 1])
                        if self._dst_kind == "planar" and d_vals else None)
        # 源侧：大地 → 整组灰；带号 → L0 灰锁定；普通平面 → 可用
        src_on = self._src_kind == "planar"
        for k in ("l0", "y0", "x0", "h0", "rig", "app"):
            self.src_cs[k].setEnabled(src_on and not (self._zone_s and k == "l0"))
        self.src_cs["zone"].setText(
            f"已识别源坐标含带号：{self._zone_s['band']} 带号{self._zone_s['zone']}，"
            f"L0 自动锁定 {self._zone_s['l0']:g}°" if self._zone_s else "")
        if self._zone_s:
            self.src_cs["l0"].setText(f"{self._zone_s['l0']:.8f}")
        dst_on = self._dst_kind == "planar"
        for k in ("l0", "y0", "x0", "h0", "rig", "app"):
            self.dst_cs[k].setEnabled(dst_on and not (self._zone_t and k == "l0"))
        self.dst_cs["zone"].setText(
            f"已识别目标坐标含带号：{self._zone_t['band']} 带号{self._zone_t['zone']}，"
            f"L0 自动锁定 {self._zone_t['l0']:g}°" if self._zone_t else "")
        if self._zone_t:
            self.dst_cs["l0"].setText(f"{self._zone_t['l0']:.8f}")
        # 表头随源/目标空间切换
        su = "B(°)/L(°)" if self._src_kind == "lonlat" else "x₁(m)/y₁(m)"
        du = "B₂(°)/L₂(°)" if self._dst_kind == "lonlat" else "x₂(m)/y₂(m)"
        self.table.setHorizontalHeaderLabels(
            ["点号", f"源{su.split('/')[0]}", f"源{su.split('/')[1]}", "源h/H",
             f"目标{du.split('/')[0]}", f"目标{du.split('/')[1]}", "目标h/H"])
        # 由多项式第三分量：仅解锁的三维模型可用；选二维时强制灰并清除勾选
        poly3_ok = self.rb_model3.isEnabled() and self.rb_model3.isChecked()
        self.chk_h_poly.setEnabled(poly3_ok)
        if not poly3_ok and self.chk_h_poly.isChecked():
            self.chk_h_poly.blockSignals(True)
            self.chk_h_poly.setChecked(False)
            self.chk_h_poly.blockSignals(False)
        self.geoid_combo.setEnabled(self.chk_h_geoid.isChecked())

    # ---- 模型/表格 ----
    def import_pairs(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择公共点对文件", "",
            "所有支持的点对文件 (*.cot *.csv *.txt *.xlsx *.xlsm);;"
            "GNSS手簿点对 (*.cot);;"
            "南方CASS点对 (*.txt);;"
            "南方坐标转换软件点对 (*.txt);;"
            "Excel 表格 (*.xlsx *.xlsm);;"
            "CSV 表格 (*.csv)")
        if path:
            self.load_file(path)

    def load_file(self, path: str):
        """导入公共点对：手簿.cot（源=大地）/ CASS / 南方7列 / 通用表格。

        列序统一为：源1,源2,源h,目标1,目标2,目标h；大地源 B 前 L 后。
        """
        from app.common import pair_io
        rows = None
        if Path(path).suffix.lower() == ".cot":
            try:
                pts = pair_io.parse_cot(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            rows = [(p.name, p.b_deg, p.l_deg, p.h_ell, p.x, p.y, p.h_normal) for p in pts]
        elif pair_io.looks_like_cass(path):
            try:
                pairs = pair_io.parse_cass(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            rows = [(f"CASS{i+1}", p.x1, p.y1, p.h1, p.x2, p.y2, p.h2)
                    for i, p in enumerate(pairs)]
        elif pair_io.looks_like_south_pairs(path):
            try:
                sp = pair_io.parse_south_pairs(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            rows = [(p.name, p.x1, p.y1, p.h1, p.x2, p.y2, p.h2) for p in sp]
        if rows is None:
            try:
                pts = read_points_csv(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            rows = []
            for p in pts:
                has_bl = p.get("B") is not None and p.get("L") is not None
                s1, s2 = (p.get("B"), p.get("L")) if has_bl else (p.get("x"), p.get("y"))
                d1 = p.get("x2") if p.get("x2") is not None else p.get("x")
                d2 = p.get("y2") if p.get("y2") is not None else p.get("y")
                sh = p.get("H_ell") if has_bl else p.get("h")
                rows.append((p.get("name"), s1, s2, sh, d1, d2, p.get("H")))
        self.table.setRowCount(0)
        for row in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            for j, val in enumerate(row):
                if isinstance(val, str):
                    txt = val
                elif val is None:
                    txt = ""
                else:
                    txt = f"{float(val):.6f}" if abs(float(val)) < 360.5 else f"{float(val):.9f}"
                self.table.setItem(r, j, QTableWidgetItem(txt))
        self.table.insertRow(self.table.rowCount())  # 末尾留空行便于续输
        # 按数据特征自动选模型：源列像经纬度 → 大地源三维模型
        import numpy as _np
        s12 = []
        for r_ in rows:
            try:
                if r_[1] is not None and r_[2] is not None:
                    s12.append([float(r_[1]), float(r_[2])])
            except (TypeError, ValueError):
                continue
        if len(s12) >= 2 and _np.max(_np.abs(_np.asarray(s12))) <= 360.5:
            pass   # 源像经纬度：由 _sync_poly_grey 切换表头与灰显
        self._model = None
        self.res_table.setRowCount(0)
        self.summary.setText("（未拟合）")
        self._sync_poly_grey()

    def _del_rows(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _clear(self):
        self.table.setRowCount(0)
        self.table.insertRow(0)
        self._model = None
        self.res_table.setRowCount(0)
        self.summary.setText("（未拟合）")

    # ---- 拟合与保存 ----
    def _collect(self):
        """读点对表 → (names, src, dst)；src/dst 行 = [v1, v2, h]（B,L 或 x,y + 高程列）。"""
        names, src, dst = [], [], []
        for r in range(self.table.rowCount()):
            cells = []
            for c in range(7):
                it = self.table.item(r, c)
                cells.append(it.text().strip() if it else "")
            if not any(cells[1:]):
                continue
            if not (cells[1] and cells[2] and cells[4] and cells[5]):
                raise ValueError(f"第 {r + 1} 行数据不完整：源与目标的前两列都必须有值")
            try:
                sv = [float(cells[1]), float(cells[2]), float(cells[3]) if cells[3] else 0.0]
                dv = [float(cells[4]), float(cells[5]), float(cells[6]) if cells[6] else 0.0]
            except ValueError:
                raise ValueError(f"第 {r + 1} 行坐标不是数值") from None
            names.append(cells[0] or f"P{r + 1}")
            src.append(sv)
            dst.append(dv)
        if len(src) < 4:
            raise ValueError("有效点对不足（至少 4 点）；点数越多、分布越均匀，多项式拟合才越可靠")
        return names, src, dst

    def _invert_planar_to_blh(self, xs, ys, hs, proj, ell):
        """普通平面 + 该侧投影 → 大地 (B, L, H)（带号自动剥离）。"""
        from app.core.projection import inverse_gauss_points
        zone = proj.get("zone")
        ys_in = [y - zone * 1_000_000 for y in ys] if zone else ys
        blh = inverse_gauss_points(xs, ys_in, float(proj["l0"]), ell,
                                   x0_m=float(proj.get("x0_m", 0.0) or 0.0),
                                   y0_m=float(proj.get("y0_m", 500000.0) or 500000.0),
                                   h0=float(proj.get("h0", 0.0) or 0.0),
                                   rigorous=bool(proj.get("rigorous", False)))
        return [(b, l, h) for (b, l), h in zip(blh, hs)]

    def fit_and_save(self):
        from app.logic import polynomial as _pl
        name = self.name_edit.text().strip()
        if not name:
            _quiet_info(self, "提示", "请先填写参数名；保存后可在“参数应用转换”页选用")
            return
        try:
            from app.core.ellipsoid import get_ellipsoid
            names, src, dst = self._collect()
            ndim = 3 if self.rb_model3.isChecked() else 2
            proj_s = self._poly_proj_values("src")
            proj_t = self._poly_proj_values("dst")
            ell_s = get_ellipsoid(self.src_cs["ell"].currentText())
            ell_d = get_ellipsoid(self.dst_cs["ell"].currentText())
            if ell_s is None or ell_d is None:
                raise ValueError("所选椭球不存在（可能已被删除），请重新选择")
            # 模型输入/输出空间：列像经纬度 → lonlat；普通平面 + 该侧投影已填 → lonlat（反算）
            s_model = "lonlat" if (self._src_kind == "lonlat" or proj_s["l0"] is not None) else "planar"
            d_model = "lonlat" if (self._dst_kind == "lonlat" or proj_t["l0"] is not None) else "planar"
            # 源/目标归算到模型空间
            if s_model == "lonlat" and self._src_kind == "planar":
                src = self._invert_planar_to_blh([p[0] for p in src], [p[1] for p in src],
                                                 [p[2] for p in src], proj_s, ell_s)
            if d_model == "lonlat" and self._dst_kind == "planar":
                dst = self._invert_planar_to_blh([p[0] for p in dst], [p[1] for p in dst],
                                                 [p[2] for p in dst], proj_t, ell_d)
            if s_model == "lonlat":
                for p in src:      # (B, L, H) 度
                    if abs(p[0]) > 90.5 or abs(p[1]) > 360.5:
                        raise ValueError("源坐标量程不像大地坐标（应 |B|≤90°、|L|≤360°），请核对数据或投影参数")
            # 高程处理：曲面拟合与大地水准面可叠加（先 ξ 换算再拟合剩余趋势）；
            # 多项式第三分量与两者互斥；不计算=透传
            use_geoid = self.chk_h_geoid.isChecked()
            use_surf = self.chk_h_surf.isChecked()
            use_poly = self.chk_h_poly.isChecked() and ndim == 3
            if (use_geoid or use_surf) and s_model != "lonlat" and proj_s["l0"] is None:
                raise ValueError("曲面拟合/大地水准面需要源侧经纬度：源为大地坐标或填写源侧投影")
            if use_geoid:
                gname = self.geoid_combo.currentText()
                if not gname:
                    raise ValueError("请先在“大地水准面模型”下拉中选择格网模型")
            src_a = np.asarray(src, dtype=float)
            dst_a = np.asarray(dst, dtype=float)
            # 多项式第三分量未勾（或二维）时，高程列不进模型
            dst_fit = dst_a if (use_poly and ndim == 3) else dst_a[:, :2]
            degree = self.degree_spin.value()
            kind = "poly2d" if ndim == 2 else "poly3d"
            model = _pl.fit_poly(src_a[:, :ndim], dst_fit, degree, kind)
        except ValueError as e:
            _quiet_warn(self, "拟合失败", str(e))
            return
        # 高程链残差：基准高 = 源h − ξ(若勾格网)；曲面拟合目标 = 目标h − 基准高
        h_rms = h_max = None
        base_h = src_a[:, 2].copy()
        gmodel = None
        if use_geoid:
            from app.core.geoid import GridModel, available_models, load_grid
            for m in available_models(MODELS_DIR):
                if m["name"] == self.geoid_combo.currentText() and m["usable"]:
                    gmodel = GridModel(load_grid(m["path"]))
                    break
            if gmodel is None:
                _quiet_warn(self, "拟合失败", "所选大地水准面格网模型不可用")
                return
            xi = []
            for b, l in zip(src_a[:, 0], src_a[:, 1]):   # 源空间=lonlat：(B, L)
                x_ = gmodel.try_undulation(float(l), float(b))
                if x_ is None:
                    _quiet_warn(self, "拟合失败", f"源点 ({b:.4f}, {l:.4f}) 超出格网模型覆盖范围")
                    return
                xi.append(float(x_))
            base_h = src_a[:, 2] - np.asarray(xi, dtype=float)
        if use_surf:
            from app.core.heightfit import fit_height, eval_height
            dh_fit = dst_a[:, 2] - base_h
            coords = (src_a[:, [1, 0]] if s_model == "lonlat" else src_a[:, :2])
            try:
                hf = fit_height(coords, dh_fit, "quadratic", "dh",
                                "lonlat" if s_model == "lonlat" else "planar")
            except ValueError as e:
                _quiet_warn(self, "曲面拟合失败", str(e))
                return
            pred = np.asarray(eval_height(hf, coords[:, 0], coords[:, 1]), dtype=float)
            hr = dh_fit - pred
            h_rms = float(np.sqrt(np.mean(hr ** 2)))
            h_max = float(np.max(np.abs(hr)))
        elif use_geoid:
            hr = dst_a[:, 2] - base_h                    # 纯格网：换算后直接比目标 h
            h_rms = float(np.sqrt(np.mean(hr ** 2)))
            h_max = float(np.max(np.abs(hr)))
        res = _pl.residual_table(src_a[:, :ndim], dst_fit, model)
        summ = _pl.residual_summary(res)
        self.res_table.setRowCount(0)
        for nm, rr in zip(names, res):
            r_ = self.res_table.rowCount()
            self.res_table.insertRow(r_)
            dy = rr[1] if len(rr) >= 2 else None
            for j, txt in enumerate([nm, f"{rr[0]:.4f}",
                                     "" if dy is None else f"{dy:.4f}", "",
                                     "" if dy is None else f"{math.hypot(rr[0], dy):.4f}"]):
                self.res_table.setItem(r_, j, QTableWidgetItem(txt))
        self._model = model
        # 序列化投影（JSON 安全；b0=参与点平均纬度供严密模式复现）
        b0_s = float(np.mean([p[1] for p in src])) if s_model == "lonlat" else 30.0
        b0_t = float(np.mean([p[1] for p in dst])) if d_model == "lonlat" else 30.0

        def _ser(proj, b0, zone):
            if proj["l0"] is None:
                return None
            b0_eff = float(proj.get("b0_manual") or b0)   # 用户显式 B0 优先
            return {"l0": float(proj["l0"]), "x0": float(proj.get("x0_m", 0.0) or 0.0),
                    "y0": float(proj.get("y0_m", 500000.0) or 500000.0), "k": 1.0,
                    "h0": float(proj.get("h0", 0.0) or 0.0),
                    "rigorous": bool(proj.get("rigorous", False)), "b0": b0_eff,
                    "zone": zone["zone"] if zone else None,
                    "band": zone["band"] if zone else None}
        doc = {"name": name, "created": datetime.now().isoformat(timespec="seconds"),
               "kind": kind, "ellipsoid": self.src_cs["ell"].currentText(),
               "ellipsoid_src": self.src_cs["ell"].currentText(),
               "ellipsoid_dst": self.dst_cs["ell"].currentText(),
               "source_kind": s_model, "target_kind": d_model,
               "projection_src": _ser(proj_s, b0_s, self._zone_s),
               "projection": _ser(proj_t, b0_t, self._zone_t),
               "poly": model.to_doc(),
               "height_mode": ("poly" if use_poly else
                               "geoid+surface" if (use_geoid and use_surf) else
                               "surface" if use_surf else
                               "geoid" if use_geoid else "none"),
               "height_flags": {"poly": use_poly, "surface": use_surf, "geoid": use_geoid},
               "heightfit": ({"mode": hf.mode, "space": hf.space, "value_type": hf.value_type,
                              "coef": [float(c) for c in hf.coef],
                              "center": [float(hf.center[0]), float(hf.center[1])],
                              "loo_rms": hf.loo_rms} if use_surf else None),
               "geoid_grid": (self.geoid_combo.currentText() if use_geoid else None),
               "accuracy": {"n_points": summ["n"], "rms_xy_m": summ["rms_xy_m"],
                            "rms_h_m": h_rms if h_rms is not None else summ["rms_h_m"],
                            "note": POLY_DISCLAIMER}}
        try:
            p = ParamLibrary(PARAMS_DIR).save(doc)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "保存失败", str(e))
            return
        h_tag = "+".join([t for t, on in (("多项式", use_poly), ("格网", use_geoid),
                                          ("曲面", use_surf)) if on]) or "透传"
        _oplog().log("poly_fit_save", {"param": name, "kind": kind, "degree": degree,
                                       "n": summ["n"], "rms_xy_m": summ["rms_xy_m"],
                                       "height": h_tag})
        txt = (f"已保存参数「{name}」（{Path(p).name}）    点数 {summ['n']}    "
               f"模型 {s_model}→{d_model} {degree} 阶    "
               f"RMS平面 {summ['rms_xy_m']:.4f} m")
        if h_rms is not None:
            txt += f"    RMS高程[{h_tag}] {h_rms:.4f} m"
        elif use_poly and summ["rms_h_m"] is not None:
            txt += f"    RMS高程 {summ['rms_h_m']:.4f} m"
        self.summary.setText(txt)


# ============================================================ CAD 转换

class CadPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("文件坐标与高程转换", "DXF / SHP / MDB(ESRI PGDB，含南方 iData) / DWG(经本机 AutoCAD 自动转 DXF) / "
                                            "文本点文件（CASS .dat、txt、csv、xlsx）；平面转换（四参数/二维七参数）与"
                                            "高程转换可独立或一并执行；输出文件不覆盖原件。"
                                            "WT/WL/WP/WN/MPJ/EDB 待样例文件验证后开放；影像/表面高程/三维(LAS) 属栅格点云重采样，另行开发。"))
        g1 = QGroupBox("1. 选择文件（可多次追加；支持目录及子目录扫描）")
        gv1 = QVBoxLayout(g1)
        r0 = QHBoxLayout()
        self.btn_files = QPushButton("选择文件")
        self.btn_files.setProperty("class", "secondary")
        self.btn_folder = QPushButton("增加文件夹")
        self.btn_folder.setProperty("class", "secondary")
        self.btn_clear_files = QPushButton("清空列表")
        self.btn_clear_files.setProperty("class", "secondary")
        r0.addWidget(self.btn_files)
        r0.addWidget(self.btn_folder)
        r0.addWidget(self.btn_clear_files)
        r0.addStretch(1)
        gv1.addLayout(r0)
        self.listw = QListWidget()
        self.listw.setMaximumHeight(150)
        gv1.addWidget(self.listw)
        v.addWidget(g1)
        g2 = QGroupBox("2. 转换设置")
        gv2 = QGridLayout(g2)
        self.planar_combo = QComboBox()
        self.height_combo = QComboBox()
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setDecimals(3)
        self.offset_spin.setSuffix(" m")
        self.apply2d = QCheckBox("2D实体也写高程(elevation)")
        self.btn_out = QPushButton("输出目录")
        self.out_edit = QLineEdit()
        gv2.addWidget(QLabel("平面参数"), 0, 0)
        gv2.addWidget(self.planar_combo, 0, 1)
        gv2.addWidget(QLabel("高程方式"), 0, 2)
        gv2.addWidget(self.height_combo, 0, 3)
        gv2.addWidget(QLabel("常数偏移"), 1, 0)
        gv2.addWidget(self.offset_spin, 1, 1)
        gv2.addWidget(self.apply2d, 1, 2)
        gv2.addWidget(QLabel("输出目录"), 2, 0)
        gv2.addWidget(self.out_edit, 2, 1)
        gv2.addWidget(self.btn_out, 2, 2)
        v.addWidget(g2)
        g3 = QGroupBox("3. 执行")
        gv3 = QVBoxLayout(g3)
        r3 = QHBoxLayout()
        self.btn_start = QPushButton("开始转换")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setEnabled(False)
        r3.addWidget(self.btn_start)
        r3.addWidget(self.btn_cancel)
        r3.addStretch(1)
        gv3.addLayout(r3)
        self.bar = QProgressBar()
        gv3.addWidget(self.bar)
        self.report = QTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("转换报告")
        gv3.addWidget(self.report)
        v.addWidget(g3, 3)
        self.btn_files.clicked.connect(self.pick_files)
        self.btn_folder.clicked.connect(self.add_folder)
        self.btn_clear_files.clicked.connect(self.listw.clear)
        self.btn_out.clicked.connect(self.pick_out)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel.clicked.connect(self.cancel)
        self.worker = None
        self._quiet = False
        self.reload_params()

    def reload_params(self):
        """刷新参数下拉。平面收 planar4 与二维七参数；高程收全部拟合
        （经纬度空间拟合经参数自带投影反算平面→BL 后求值，需参数含投影）。"""
        self.planar_combo.clear()
        self.planar_combo.addItem("不转换平面", None)
        self.height_combo.clear()
        self.height_combo.addItem("不转换高程", {"mode": "none"})
        lib = ParamLibrary(PARAMS_DIR)
        for p in lib.list():
            try:
                d = lib.load(p["name"])
            except Exception:  # noqa: BLE001
                continue
            is_7d = d.get("kind") == "seven2d" and bool(d.get("seven"))
            if d.get("planar4") or is_7d:
                tag = "（二维七参数）" if is_7d and not d.get("planar4") else ""
                self.planar_combo.addItem(f"平面: {d['name']}{tag}", d)
            hf = d.get("heightfit")
            if hf and (hf.get("space", "planar") == "planar" or d.get("projection")):
                tag = "（经纬度拟合）" if hf.get("space") != "planar" else ""
                self.height_combo.addItem(f"高程: {d['name']}{tag}", {"mode": "param", "param": d})
        self.height_combo.addItem("常数偏移", {"mode": "offset"})

    def showEvent(self, e):
        """页面显示时刷新参数下拉（参数可能在控制点页/参数库刚保存）。"""
        super().showEvent(e)
        try:
            self.reload_params()
        except Exception:  # noqa: BLE001
            pass

    _SUPPORTED_EXTS = (".dxf", ".shp", ".mdb", ".dwg", ".dat", ".txt", ".csv", ".xlsx")

    def _append_files(self, files) -> int:
        """追加文件（去重），返回新增数。"""
        added = 0
        have = {self.listw.item(i).text() for i in range(self.listw.count())}
        for f in files:
            if f and f not in have:
                self.listw.addItem(f)
                have.add(f)
                added += 1
        return added

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择转换文件（可按住 Ctrl/Shift 多选，可多次追加）", "",
            "支持格式 (*.dxf *.shp *.mdb *.dwg *.dat *.txt *.csv *.xlsx);;"
            "DXF (*.dxf);;SHP (*.shp);;MDB/PGDB (*.mdb);;DWG (*.dwg);;文本/表格 (*.dat *.txt *.csv *.xlsx);;"
            "待样例开放 (*.wt *.wl *.wp *.wn *.mpj *.edb)")
        if self._append_files(files) == 0 and files:
            _quiet_info(self, "提示", "所选文件均已在列表中")

    def add_folder(self, d: str | None = None):
        """递归扫描目录及子目录下全部支持格式文件并追加（去重）。
        d 为空时弹目录选择框（供按钮调用；测试/代码可直接传路径）。"""
        if not d:
            d = QFileDialog.getExistingDirectory(self, "选择文件夹（含子目录）")
            if not d:
                return
        found = []
        for root, _dirs, names in os.walk(d):
            for nm in names:
                if nm.lower().endswith(self._SUPPORTED_EXTS):
                    found.append(os.path.join(root, nm))
        added = self._append_files(sorted(found))
        if added:
            _quiet_info(self, "已追加", f"从文件夹扫描到 {len(found)} 个支持文件，新增 {added} 个")
        else:
            _quiet_info(self, "提示", f"文件夹内未发现新的支持文件（扫描到 {len(found)} 个，均在列表中）" if found
                        else "文件夹内未发现支持格式的文件")

    def pick_out(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self.out_edit.setText(d)

    def start(self):
        files = [self.listw.item(i).text() for i in range(self.listw.count())]
        if not files:
            _quiet_info(self, "提示", "请先选择文件")
            return
        out_dir = self.out_edit.text().strip() or str(Path(files[0]).parent)
        hdata = dict(self.height_combo.currentData() or {"mode": "none"})
        if hdata.get("mode") == "offset":
            hdata["offset"] = self.offset_spin.value()
        hname = {"none": "不转换",
                 "offset": f"偏移{self.offset_spin.value():+.3f}m"}.get(
            hdata.get("mode"), f"参数:{(hdata.get('param') or {}).get('name')}")
        jobs, jerr = _fj.build_jobs(files, out_dir, self.planar_combo.currentData(),
                                    hdata, self.apply2d.isChecked())
        if jerr:
            _quiet_warn(self, "无法开始转换", jerr)
            return
        self.report.clear()
        self.bar.setValue(0)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker = CadWorker(jobs)
        self.worker.progress.connect(lambda d, t, c: (self.bar.setMaximum(t), self.bar.setValue(d)))
        self.worker.one_done.connect(self._on_one)
        self.worker.finished_ok.connect(self._on_done)
        self.worker.failed.connect(lambda e: _quiet_warn(self, "失败", e))
        self.worker.start()
        self._hname = hname

    def cancel(self):
        if self.worker:
            self.worker.cancel()

    def _on_one(self, item: dict):
        if "error" in item:
            self.report.append(f"[失败] {Path(item['file']).name}: {item['error']}")
            return
        r = item["result"]
        if hasattr(r, "features"):
            extra = "；".join(r.log[:-1]) if len(r.log) > 1 else ""
            self.report.append(f"[完成] {Path(item['file']).name} → {len(r.out_paths)} 个 SHP  "
                               f"要素类 {r.transformed}/{r.total}，要素 {r.features}"
                               + (f"，未支持 {sum(r.unsupported.values())}" if r.unsupported else ""))
            if extra:
                self.report.append("    " + extra)
        else:
            self.report.append(f"[完成] {Path(item['file']).name} → {Path(r.out_path).name}  "
                               f"实体 {r.transformed}/{r.total}，未支持 {sum(r.unsupported.values())}")
        if r.unsupported:
            self.report.append("    " + ", ".join(f"{k}×{v}" for k, v in r.unsupported.items()))

    def _on_done(self, ok: int):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        _oplog().log("cad_transform", {"files": ok, "planar": self.planar_combo.currentText(),
                                       "height": self._hname})
        _quiet_info(self, "转换完成", f"成功 {ok} 个文件")


# ============================================================ 精度对比
class BarChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data = []
        self.setMinimumHeight(160)

    def set_data(self, data: list):
        self.data = data
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        maxv = max(v for _, v, _ in self.data) or 1.0
        n = len(self.data)
        bw = min(64, (w - 60) / max(n, 1) * 0.6)
        p.setFont(QFont("Microsoft YaHei UI", 8))
        for i, (label, v, color) in enumerate(self.data):
            x = 40 + i * (w - 80) / n + ((w - 80) / n - bw) / 2
            bh = (h - 60) * v / maxv
            p.setBrush(QColor(color))
            p.drawRect(int(x), int(h - 30 - bh), int(bw), int(bh))
            p.setPen(QColor("#1e2a32"))
            p.drawText(int(x - 6), h - 14, label)
            p.drawText(int(x - 6), int(h - 34 - bh), f"{v:.3f}")
        p.end()


class AccuracyPage(QWidget):
    """精度对比：点对文件 × 两个转换参数 → 残差统计与建议（逻辑在 app/logic/accuracy.py）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("精度对比", "导入点对文件（手簿 .cot / CASS .txt / 对比点 .csv）→ 选择两个转换参数 → "
                                      "勾选对比坐标/对比高程 → 输出残差统计与建议。"))
        g1 = QGroupBox("1. 点对文件")
        gv1 = QVBoxLayout(g1)
        r1 = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.btn_load = QPushButton("导入点对文件")
        self.btn_load.setProperty("class", "secondary")
        r1.addWidget(self.path_edit, 1)
        r1.addWidget(self.btn_load)
        gv1.addLayout(r1)
        self.lbl_pairs = QLabel("未加载")
        self.lbl_pairs.setObjectName("muted")
        gv1.addWidget(self.lbl_pairs)
        self.pairs_table = QTableWidget(0, 7)
        self.pairs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.pairs_table.setAlternatingRowColors(True)
        self.pairs_table.setMaximumHeight(170)
        gv1.addWidget(self.pairs_table)
        v.addWidget(g1)
        g2 = QGroupBox("2. 对比设置（参数来自控制点转换保存的参数库；纯大地水准面参数亦可对比）")
        gv2 = QGridLayout(g2)
        self.combo_a = QComboBox()
        self.combo_b = QComboBox()
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setProperty("class", "secondary")
        self.chk_plane = QCheckBox("对比坐标")
        self.chk_plane.setChecked(True)
        self.chk_height = QCheckBox("对比高程")
        self.chk_height.setChecked(True)
        self.chk_plane.setToolTip("逐点比较两个参数换出的平面坐标残差（需要点对含目标平面坐标）")
        self.chk_height.setToolTip("逐点比较两个参数换出的高程残差；LOO 越小代表外推越可靠")
        self.btn_run = QPushButton("计算对比")
        gv2.addWidget(QLabel("参数 A"), 0, 0)
        gv2.addWidget(self.combo_a, 0, 1)
        gv2.addWidget(self.btn_refresh, 0, 2)
        gv2.addWidget(QLabel("参数 B"), 1, 0)
        gv2.addWidget(self.combo_b, 1, 1)
        gv2.addWidget(self.btn_run, 1, 2)
        gv2.addWidget(self.chk_plane, 2, 0)
        gv2.addWidget(self.chk_height, 2, 1)
        v.addWidget(g2)
        self.verdict = QLabel("（结论将显示在这里）")
        self.verdict.setWordWrap(True)
        self.verdict.setStyleSheet("font-weight:bold; color:#225a75;")
        v.addWidget(self.verdict)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["参数", "点数", "RMS平面(m)", "平均平面(m)", "最大平面(m)",
                                              "RMS高程(m)", "平均高程(m)", "最大高程(m)", "说明"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        v.addWidget(self.table, 2)
        self.chart = BarChart()
        self.chart.setMaximumHeight(150)
        v.addWidget(self.chart)
        r = QHBoxLayout()
        self.btn_csv = QPushButton("导出 CSV")
        self.btn_csv.setProperty("class", "secondary")
        r.addWidget(self.btn_csv)
        r.addStretch(1)
        v.addLayout(r)
        self.btn_load.clicked.connect(self.load)
        self.btn_run.clicked.connect(self.run_compare)
        self.btn_refresh.clicked.connect(self.reload_params)
        self.btn_csv.clicked.connect(self.export_csv)
        self._pairs = None
        self._results = []
        self._quiet = False
        self.reload_params()

    def showEvent(self, e):
        """页面显示时刷新参数下拉（控制点页新保存的参数即时可选）。"""
        super().showEvent(e)
        try:
            self.reload_params()
        except Exception:  # noqa: BLE001
            pass

    def reload_params(self):
        for c in (self.combo_a, self.combo_b):
            c.blockSignals(True)
            cur = c.currentData()
            c.clear()
            c.addItem("（未选择）", None)
            for p in ParamLibrary(PARAMS_DIR).list():
                c.addItem(f"{p['name']}  [{p['kind']}]", p["name"])
            if cur is not None:
                idx = c.findData(cur)
                if idx >= 0:
                    c.setCurrentIndex(idx)
            c.blockSignals(False)

    def load(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择点对文件", "",
            "点对文件 (*.cot *.txt *.csv *.dat);;手簿 .cot (*.cot);;CASS 公共点 (*.txt);;对比点 CSV (*.csv)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str):
        try:
            pairs = _acc.load_pairs(path)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "加载失败", str(e))
            return
        self._pairs = pairs
        self.path_edit.setText(path)
        self.lbl_pairs.setText(
            f"{pairs.source}    共 {len(pairs.names)} 点"
            + ("    含目标平面坐标（可对比坐标+高程）" if pairs.dst is not None
               else "    无目标平面坐标（仅高程对比）"))
        # 回填预览表
        if pairs.dst is not None:
            h1 = ["经度", "纬度"] if pairs.src_kind == "lonlat" else ["x(北)", "y(东)"]
            headers = ["点名", f"源{h1[0]}", f"源{h1[1]}", "源h", "目标x(北)", "目标y(东)", "目标h"]
            rows = [[pairs.names[i], pairs.src[i, 0], pairs.src[i, 1], pairs.src_h[i],
                     pairs.dst[i, 0], pairs.dst[i, 1], pairs.dst_h[i]]
                    for i in range(len(pairs.names))]
        else:
            headers = ["点名", "经度", "纬度", "H_ell", "H_normal"]
            rows = [[pairs.names[i], pairs.src[i, 0], pairs.src[i, 1], pairs.src_h[i], pairs.dst_h[i]]
                    for i in range(len(pairs.names))]
        self.pairs_table.setColumnCount(len(headers))
        self.pairs_table.setHorizontalHeaderLabels(headers)
        self.pairs_table.setRowCount(0)
        for row in rows:
            r = self.pairs_table.rowCount()
            self.pairs_table.insertRow(r)
            for j, val in enumerate(row):
                txt = val if isinstance(val, str) else ("" if val is None else f"{float(val):.6f}")
                self.pairs_table.setItem(r, j, QTableWidgetItem(txt))
        self.pairs_table.resizeColumnsToContents()

    def _selected_docs(self) -> list[tuple[str, dict | None, str | None]]:
        """读两个参数下拉 → [(显示名, doc|None, 错误)]；损坏文件不抛出。"""
        docs = []
        for c in (self.combo_a, self.combo_b):
            name = c.currentData()
            if not name:
                docs.append(("（未选择）", None, None))
                continue
            try:
                docs.append((name, ParamLibrary(PARAMS_DIR).load(name), None))
            except Exception as e:  # noqa: BLE001
                docs.append((name, None, str(e)))
        return docs

    def run_compare(self):
        if self._pairs is None:
            _quiet_info(self, "提示", "请先导入点对文件")
            return
        if not (self.chk_plane.isChecked() or self.chk_height.isChecked()):
            _quiet_info(self, "提示", "请至少勾选“对比坐标”或“对比高程”之一")
            return
        docs = []
        for name, doc, err in self._selected_docs():
            if err:
                _quiet_warn(self, "参数无法使用", f"{name}：{err}")
                return
            docs.append((name, doc))
        try:
            rows, verdict = _acc.compare(self._pairs, docs,
                                         self.chk_plane.isChecked(),
                                         self.chk_height.isChecked(), MODELS_DIR)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "对比失败", str(e))
            return
        self._results = rows
        # 结果表
        data = []
        for r in rows:
            data.append([r["name"], r.get("n_plane") or r.get("n_h") or 0,
                         r.get("rms_xy"), r.get("mean_xy"), r.get("max_xy"),
                         r.get("rms_h"), r.get("mean_h"), r.get("max_h"),
                         r.get("note", "")])
        _fill_table(self.table, ["参数", "点数", "RMS平面(m)", "平均平面(m)", "最大平面(m)",
                                 "RMS高程(m)", "平均高程(m)", "最大高程(m)", "说明"], data)
        self.verdict.setText("结论：" + verdict)
        # 柱状图：优先坐标 RMS，未勾则高程 RMS
        metric_idx = 2 if self.chk_plane.isChecked() else 5
        chart_data = []
        for r in rows:
            v = r.get("rms_xy") if self.chk_plane.isChecked() else r.get("rms_h")
            if v is None:
                v = 0.0
            chart_data.append((r["name"][:10], v, ACCENT if self.chk_plane.isChecked() else ACCENT2))
        self.chart.set_data(chart_data)
        _oplog().log("accuracy_compare", {"pairs": self.path_edit.text(),
                                          "a": docs[0][0], "b": docs[1][0],
                                          "plane": self.chk_plane.isChecked(),
                                          "height": self.chk_height.isChecked(),
                                          "verdict": verdict})

    def export_csv(self):
        if not self._results:
            _quiet_info(self, "提示", "没有可导出的对比结果")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出对比结果", "精度对比.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("参数,点数,RMS平面(m),平均平面(m),最大平面(m),RMS高程(m),平均高程(m),最大高程(m),说明\n")
            for r in self._results:
                f.write(",".join([
                    r["name"], str(r.get("n_plane") or r.get("n_h") or 0),
                    "" if r.get("rms_xy") is None else f"{r['rms_xy']:.4f}",
                    "" if r.get("mean_xy") is None else f"{r['mean_xy']:.4f}",
                    "" if r.get("max_xy") is None else f"{r['max_xy']:.4f}",
                    "" if r.get("rms_h") is None else f"{r['rms_h']:.4f}",
                    "" if r.get("mean_h") is None else f"{r['mean_h']:.4f}",
                    "" if r.get("max_h") is None else f"{r['max_h']:.4f}",
                    str(r.get("note", "")),]) + "\n")
        _quiet_info(self, "已导出", path)


class ParamLibraryPage(QWidget):
    """参数库：椭球参数 / 坐标转换参数 / 大地水准面文件 三分区。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("参数库", "椭球参数（自定义椭球保存后各页下拉即可选）· 坐标转换参数 · "
                                      "大地水准面文件。支持导入南方 .ipj 方案。"))
        # ---- ① 椭球参数 ----
        g1 = QGroupBox("① 椭球参数（自定义）")
        v1 = QVBoxLayout(g1)
        self.listw_ell = QListWidget()
        v1.addWidget(self.listw_ell, 1)
        r1 = QHBoxLayout()
        self.btn_ell_add = QPushButton("新增椭球")
        self.btn_ell_del = QPushButton("删除")
        self.btn_ell_dir = QPushButton("打开存放文件夹")
        for b in (self.btn_ell_add, self.btn_ell_del, self.btn_ell_dir):
            b.setProperty("class", "secondary")
            r1.addWidget(b)
        r1.addStretch(1)
        v1.addLayout(r1)
        v.addWidget(g1, 1)
        # ---- ② 坐标转换参数 ----
        g2 = QGroupBox("② 坐标转换参数（JSON，照片POS/文件转换/精度对比各页调用）")
        v2 = QVBoxLayout(g2)
        self.listw = QListWidget()
        v2.addWidget(self.listw, 1)
        r2 = QHBoxLayout()
        self.btn_view = QPushButton("查看 JSON")
        self.btn_del = QPushButton("删除")
        self.btn_refresh = QPushButton("刷新")
        self.btn_ipj = QPushButton("导入南方方案(.ipj)")
        self.btn_ipj.setProperty("class", "secondary")
        self.btn_param_dir = QPushButton("打开存放文件夹")
        for b in (self.btn_view, self.btn_del, self.btn_refresh):
            r2.addWidget(b)
        r2.addWidget(self.btn_ipj)
        r2.addWidget(self.btn_param_dir)
        r2.addStretch(1)
        v2.addLayout(r2)
        v.addWidget(g2, 1)
        # ---- ③ 大地水准面文件 ----
        g3 = QGroupBox("③ 大地水准面文件（models/，gtx/tif/csv/zgf/ggf/grd 自动识别）")
        v3 = QVBoxLayout(g3)
        self.listw_geoid = QListWidget()
        v3.addWidget(self.listw_geoid, 1)
        r3 = QHBoxLayout()
        self.btn_geoid_refresh = QPushButton("刷新")
        self.btn_geoid_dir = QPushButton("打开存放文件夹")
        for b in (self.btn_geoid_refresh, self.btn_geoid_dir):
            b.setProperty("class", "secondary")
            r3.addWidget(b)
        r3.addStretch(1)
        v3.addLayout(r3)
        v.addWidget(g3, 1)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(110)
        self.detail.setPlaceholderText("选中条目的详细信息")
        v.addWidget(self.detail)
        # 信号
        self.btn_ell_add.clicked.connect(self.add_ellipsoid)
        self.btn_ell_del.clicked.connect(self.delete_ellipsoid)
        from app.core.ellipsoid import ellipsoid_dir
        self.btn_ell_dir.clicked.connect(lambda: self._open_dir(ellipsoid_dir()))
        self.btn_view.clicked.connect(self.view)
        self.btn_del.clicked.connect(self.delete)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_ipj.clicked.connect(self.import_ipj)
        self.btn_param_dir.clicked.connect(lambda: self._open_dir(PARAMS_DIR))
        self.btn_geoid_refresh.clicked.connect(self.reload)
        self.btn_geoid_dir.clicked.connect(lambda: self._open_dir(MODELS_DIR))
        self.listw_ell.currentRowChanged.connect(lambda _: self._show_ell_info())
        self.reload()

    # ---- 通用 ----
    def _open_dir(self, path):
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    # ---- ① 椭球 ----
    def reload(self):
        self.reload_ellipsoids()
        self.reload_params()
        self.reload_geoid()

    def reload_ellipsoids(self):
        from app.core.ellipsoid import ELLIPSOIDS, all_ellipsoids
        self.listw_ell.clear()
        all_e = all_ellipsoids()
        for nm in list(ELLIPSOIDS) + [n for n in all_e if n not in ELLIPSOIDS]:
            e = all_e[nm]
            tag = "" if nm in ELLIPSOIDS else "  [自定义]"
            self.listw_ell.addItem(f"{nm}    a={e.a:.4f}    1/f={e.inv_f:.9f}{tag}")

    def _show_ell_info(self):
        it = self.listw_ell.currentItem()
        if it:
            self.detail.setPlainText(it.text())

    def add_ellipsoid(self):
        from app.core.ellipsoid import save_custom_ellipsoid
        dlg = QDialog(self)
        dlg.setWindowTitle("新增自定义椭球")
        form = QFormLayout(dlg)
        e_name = QLineEdit()
        e_a = QLineEdit("6378137.0000")
        e_f = QLineEdit("298.257222101")
        form.addRow("名称", e_name)
        form.addRow("长半轴 a(m)", e_a)
        form.addRow("1/f", e_f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            p = save_custom_ellipsoid(e_name.text(), e_a.text(), e_f.text())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(e))
            return
        _oplog().log("ellipsoid_save", {"name": e_name.text().strip(), "file": str(p)})
        _quiet_info(self, "已保存", f"自定义椭球已保存：{p}\n各页椭球下拉选择它即可。")
        self.reload_ellipsoids()

    def delete_ellipsoid(self):
        from app.core.ellipsoid import delete_custom_ellipsoid
        it = self.listw_ell.currentItem()
        if not it:
            _quiet_info(self, "提示", "请先选择椭球")
            return
        name = it.text().split("    ")[0]
        if name in ("WGS84", "CGCS2000", "Beijing54", "Xian80"):
            _quiet_info(self, "提示", "内置椭球不可删除")
            return
        if QMessageBox.question(self, "删除椭球", f"确认删除自定义椭球「{name}」？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        delete_custom_ellipsoid(name)
        _oplog().log("ellipsoid_delete", {"name": name})
        self.reload_ellipsoids()

    # ---- ② 坐标转换参数 ----
    def reload_params(self):
        self.listw.clear()
        for p in ParamLibrary(PARAMS_DIR).list():
            self.listw.addItem(f"{p['name']}  [{p['kind']}]  RMS_xy={p.get('rms_xy_m')}")

    def import_ipj(self):
        """导入南方 icoord 方案：投影可直接应用，公共点重新拟合，Parm4/Parm7 存参考对照。"""
        path, _ = QFileDialog.getOpenFileName(self, "选择南方方案文件", "",
                                              "南方方案 (*.ipj *.db);;全部文件 (*.*)")
        if not path:
            return
        try:
            from app.fileconv.south_ipj import import_ipj
            r = import_ipj(path, PARAMS_DIR)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        parts = [f"{k}×{v}" for k, v in r.get("counts", {}).items() if v]
        msg = f"已导入 {len(r['imported'])} 项：{'；'.join(parts) if parts else '（无有效内容）'}"
        if r.get("notes"):
            msg += "\n提示：" + "；".join(r["notes"])
        if any("参考" in n for n in r["imported"]):
            msg += "\n（*_参考 项为南方原始参数对照，不可直接应用）"
        QMessageBox.information(self, "导入完成", msg)
        self.reload_params()

    def view(self):
        it = self.listw.currentItem()
        if not it:
            return
        name = it.text().split("  [")[0]
        d = ParamLibrary(PARAMS_DIR).load(name)
        import json
        self.detail.setPlainText(json.dumps(d, ensure_ascii=False, indent=2))

    def delete(self):
        it = self.listw.currentItem()
        if not it:
            _quiet_info(self, "提示", "请先选择参数")
            return
        name = it.text().split("  [")[0]
        if QMessageBox.question(self, "删除参数", f"确认删除参数「{name}」？",
                                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            ParamLibrary(PARAMS_DIR).delete(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.reload_params()
        self.detail.setPlainText(f"已删除参数：{name}")

    # ---- ③ 大地水准面 ----
    def reload_geoid(self):
        from app.core.geoid import available_models
        self.listw_geoid.clear()
        for m in available_models(MODELS_DIR):
            tag = "可用" if m["usable"] else "不可读"
            self.listw_geoid.addItem(f"{m['name']}  [{tag}]  {Path(m['path']).name}")


class LedgerPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("处理台账", "每次参数计算/照片回写/文件转换/精度报告都会记录时间、输入、参数与结果，可导出 CSV。"))
        r = QHBoxLayout()
        self.btn_refresh = QPushButton("刷新")
        self.btn_export = QPushButton("导出 CSV")
        self.btn_clear = QPushButton("清空记录")
        self.btn_clear.setToolTip("删除全部台账记录（logs/operation_log.jsonl），不可恢复；建议先导出 CSV 备份")
        r.addWidget(self.btn_refresh)
        r.addWidget(self.btn_export)
        r.addWidget(self.btn_clear)
        r.addStretch(1)
        v.addLayout(r)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["时间", "操作", "摘要", "详情"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        v.addWidget(self.table, 3)
        self.btn_refresh.clicked.connect(self.reload)
        self.btn_export.clicked.connect(self.export)
        self.btn_clear.clicked.connect(self.clear_log)
        self.reload()

    def clear_log(self):
        """清空全部台账记录（确认后执行）。"""
        r = QMessageBox.question(self, "清空记录",
                                 "确认清空全部处理台账？删除后不可恢复。\n（建议先“导出 CSV”备份）",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        n = _oplog().clear()
        self.reload()
        _oplog().log("ledger_clear", {"removed": n})

    def reload(self):
        rows = _oplog().read_all()
        rows = list(reversed(rows))
        self.table.setRowCount(len(rows))
        import json
        for i, r in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(str(r.get("time", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(r.get("op", ""))))
            summary = {k: v for k, v in r.items() if k not in ("time", "op")}
            self.table.setItem(i, 2, QTableWidgetItem(
                json.dumps(summary, ensure_ascii=False)[:120]))
            self.table.setItem(i, 3, QTableWidgetItem(json.dumps(summary, ensure_ascii=False)))
        self.table.resizeColumnsToContents()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出台账", "处理台账.csv", "CSV (*.csv)")
        if not path:
            return
        p = _oplog().export_csv(path)
        QMessageBox.information(self, "已导出", p)


# ============================================================ 帮助
class HelpPage(QWidget):
    """关于本软件：版本 / 作者 / 许可证 / 技术手册 / 最新版本地址 / BUG 反馈。"""

    VERSION = "1.1"
    AUTHOR = "求道之心"
    LICENSE = "MIT License"
    REPO_URL = "https://github.com/XEMPZ/GeoRefine"
    RELEASE_URL = REPO_URL + "/releases"
    BUG_EMAIL = "jewettleah2@gmail.com"
    MANUAL = _ROOT / "docs" / "技术手册.md"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QApplication
        v = QVBoxLayout(self)
        head = QLabel(f"<b style='font-size:16pt'>GeoRefine</b>"
                      f"<span style='color:#6b7a83'>　似大地水准面精化与坐标转换软件　V{self.VERSION}</span>")
        head.setTextFormat(Qt.RichText)
        v.addWidget(head)
        v.addWidget(QLabel(f"作者：{self.AUTHOR}　　许可证：{self.LICENSE}"))

        gh = QGroupBox("帮助")
        hv = QVBoxLayout(gh)
        self.btn_manual = QPushButton("打开技术手册")
        self.btn_manual.setProperty("class", "secondary")
        self.btn_manual.clicked.connect(self.open_manual)
        hv.addWidget(self.btn_manual)
        hv.addWidget(QLabel("技术手册包含坐标约定、各算法依据、数据格式、参数库结构与精度验证结论。"))
        v.addWidget(gh)

        gu = QGroupBox("项目主页与最新版本")
        uv = QVBoxLayout(gu)
        url_label = QLabel(f'<a href="{self.REPO_URL}">{self.REPO_URL}</a>')
        url_label.setOpenExternalLinks(True)
        self.btn_copy_url = QPushButton("复制")
        self.btn_copy_url.setProperty("class", "secondary")
        self.btn_copy_url.clicked.connect(lambda: self._copy(self.REPO_URL, self.btn_copy_url, "复制"))
        ur = QHBoxLayout()
        ur.addWidget(url_label, 1)
        ur.addWidget(self.btn_copy_url)
        uv.addLayout(ur)
        uv.addWidget(QLabel("GitHub 仓库：源代码、发行版下载与问题反馈入口（MIT 许可，可自由使用/修改/分发）。"))
        v.addWidget(gu)

        gb = QGroupBox("报告 BUG")
        bv = QVBoxLayout(gb)
        er = QHBoxLayout()
        er.addWidget(QLabel(f'<a href="mailto:{self.BUG_EMAIL}">{self.BUG_EMAIL}</a>'), 1)
        self.btn_copy_mail = QPushButton("复制")
        self.btn_copy_mail.setProperty("class", "secondary")
        self.btn_copy_mail.clicked.connect(lambda: self._copy(self.BUG_EMAIL, self.btn_copy_mail, "复制"))
        er.addWidget(self.btn_copy_mail)
        bv.addLayout(er)
        bv.addWidget(QLabel("反馈时请附：操作步骤、涉及数据样例、软件版本号。"))
        v.addWidget(gb)

        tips = QLabel("坐标约定：x=北、y=东；ξ=H−h；d.ms 编码 105.302568 = 105°30′25.68″。")
        tips.setObjectName("muted")
        v.addWidget(tips)
        v.addStretch(1)

    def open_manual(self):
        """打开技术手册（不存在则先生成占位文件）。"""
        p = self.MANUAL
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "# GeoRefine 技术手册（占位）\n\n"
                "版本 V1.1 · 作者 求道之心 · 本手册为占位版本，内容持续完善中。\n\n"
                "## 坐标约定\n\n"
                "- 平面坐标 x=北坐标、y=东坐标（含 500km 假东偏移）\n"
                "- 大地坐标 B 纬度、L 经度；高程异常 ξ = H − h，正常高 = 大地高 − ξ\n"
                "- d.ms 编码：105.302568 = 105°30′25.68″\n\n"
                "## 功能说明\n\n"
                "（占位：各功能操作说明待扩充）\n\n"
                "## 常见问题\n\n"
                "（占位：FAQ 待扩充）\n",
                encoding="utf-8")
        os.startfile(str(p))

    def _copy(self, text, btn, label):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        QApplication.clipboard().setText(text)
        btn.setText("已复制")
        QTimer.singleShot(1500, lambda: btn.setText(label))


# ============================================================ 高斯投影换带
class GaussPage(QWidget):
    """高斯正反算与换带计算：UTM 尺度、加常数(km)、投影面大地高、严密/近似工程椭球。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("高斯投影换带计算", "BL/xy ↔ BL/xy：UTM 尺度 k、加常数 x0/y0(km)、投影面大地高 h0、"
                                              "严密/近似工程椭球（真值逐位验证 0.5µm）。"
                                              "角度输入支持 度.分秒 编码（105.302568 = 105°30′25.68″）、"
                                              "空格分隔度分秒（105 30 25.68）或十进制度。"))
        top = QHBoxLayout()
        g0 = QGroupBox("椭球参数")
        f0 = QGridLayout(g0)
        self.ell_combo = QComboBox()
        from app.core.ellipsoid import all_ellipsoids
        for nm in all_ellipsoids():
            self.ell_combo.addItem(nm)
        self.ell_combo.addItem("自定义")
        self.ell_combo.setCurrentText("CGCS2000")
        self.a_edit = QLineEdit("6378137.0000")
        self.invf_edit = QLineEdit("298.257223563")
        f0.addWidget(QLabel("椭球"), 0, 0)
        f0.addWidget(self.ell_combo, 0, 1)
        f0.addWidget(QLabel("a(m)"), 1, 0)
        f0.addWidget(self.a_edit, 1, 1)
        f0.addWidget(QLabel("1/f"), 2, 0)
        f0.addWidget(self.invf_edit, 2, 1)
        top.addWidget(g0)
        self.src_widgets = {}
        self.dst_widgets = {}
        g1 = self._proj_group("转换前投影参数", self.src_widgets)
        g2 = self._proj_group("转换后投影参数", self.dst_widgets)
        top.addWidget(g1, 3)
        top.addWidget(g2, 3)
        v.addLayout(top)
        io = QHBoxLayout()
        g3 = QGroupBox("输入源坐标")
        gv3 = QVBoxLayout(g3)
        self.rb_src_xy = QRadioButton("平面坐标")
        self.rb_src_bl = QRadioButton("大地坐标")
        self.rb_src_xy.setChecked(True)
        gv3.addWidget(self.rb_src_xy)
        row_bl = QHBoxLayout()
        row_bl.addWidget(self.rb_src_bl)
        # 独立容器隔离 QRadioButton 互斥域，避免与"平面/大地"互顶；格式选项与大地坐标同行
        self.fmt_src_w = QWidget()
        fr_in = QHBoxLayout(self.fmt_src_w)
        fr_in.setContentsMargins(0, 0, 0, 0)
        self.fmt_src_dms = QRadioButton("d.ms 格式")
        self.fmt_src_d = QRadioButton("d 格式")
        self.fmt_src_dms.setChecked(True)
        for r_ in (self.fmt_src_dms, self.fmt_src_d):
            fr_in.addWidget(r_)
        row_bl.addWidget(self.fmt_src_w)
        row_bl.addStretch(1)
        gv3.addLayout(row_bl)
        self.table_in = QTableWidget(0, 4)
        self.table_in.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv3.addWidget(self.table_in, 1)
        io.addWidget(g3, 1)
        g4 = QGroupBox("输出目标坐标")
        gv4 = QVBoxLayout(g4)
        self.rb_dst_xy = QRadioButton("平面坐标")
        self.rb_dst_bl = QRadioButton("大地坐标")
        self.rb_dst_xy.setChecked(True)
        gv4.addWidget(self.rb_dst_xy)
        row_bl2 = QHBoxLayout()
        row_bl2.addWidget(self.rb_dst_bl)
        self.fmt_dst_w = QWidget()
        fr_out = QHBoxLayout(self.fmt_dst_w)
        fr_out.setContentsMargins(0, 0, 0, 0)
        self.fmt_dst_dms = QRadioButton("d.ms 格式")
        self.fmt_dst_d = QRadioButton("d 格式")
        self.fmt_dst_dms.setChecked(True)
        for r_ in (self.fmt_dst_dms, self.fmt_dst_d):
            fr_out.addWidget(r_)
        row_bl2.addWidget(self.fmt_dst_w)
        row_bl2.addStretch(1)
        gv4.addLayout(row_bl2)
        self.table_out = QTableWidget(0, 4)
        self.table_out.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv4.addWidget(self.table_out, 1)
        io.addWidget(g4, 1)
        v.addLayout(io, 2)
        btns = QHBoxLayout()
        self.btn_import = QPushButton("读入源数据")
        self.btn_import.setProperty("class", "secondary")
        self.btn_clear = QPushButton("清除数据")
        self.btn_clear.setProperty("class", "secondary")
        self.btn_run = QPushButton("转换")
        self.btn_save = QPushButton("保存结果")
        self.btn_save.setProperty("class", "secondary")
        for b in (self.btn_import, self.btn_clear, self.btn_run, self.btn_save):
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        self.btn_import.clicked.connect(self.import_pts)
        self.btn_clear.clicked.connect(self.clear_rows)
        self.btn_run.clicked.connect(self.run)
        self.btn_save.clicked.connect(self.save_result)
        self.ell_combo.currentIndexChanged.connect(self._show_ell)
        for rb in (self.rb_src_xy, self.rb_src_bl, self.rb_dst_xy, self.rb_dst_bl,
                   self.fmt_src_dms, self.fmt_src_d, self.fmt_dst_dms, self.fmt_dst_d):
            rb.toggled.connect(self._update_headers)
            rb.toggled.connect(self._auto_rerun)
        self._auto = False
        for src_rb, dms_rb, d_rb in ((self.rb_src_bl, self.fmt_src_dms, self.fmt_src_d),
                                     (self.rb_dst_bl, self.fmt_dst_dms, self.fmt_dst_d)):
            def sync(on, a=dms_rb, b=d_rb):
                for w_ in (a, b):
                    w_.setEnabled(on)
                    w_.setVisible(on)
            src_rb.toggled.connect(sync)
            sync(src_rb.isChecked(), dms_rb, d_rb)
        self._enter_filter_in = _enable_enter_add(self.table_in)
        self.table_in.insertRow(0)  # 初始空行供直接键入
        self._show_ell()
        self._update_headers()
        self._result_rows = []

    def _update_headers(self):
        def hdr(side_in: bool):
            bl = self.rb_src_bl.isChecked() if side_in else self.rb_dst_bl.isChecked()
            if not bl:
                return ["点号", "x(m)", "y(m)", "H/h(m)"]
            dms = self.fmt_src_dms.isChecked() if side_in else self.fmt_dst_dms.isChecked()
            unit = "d.ms" if dms else "d"
            return ["点号", f"B({unit})", f"L({unit})", "H/h(m)"]
        self.table_in.setHorizontalHeaderLabels(hdr(True))
        self.table_out.setHorizontalHeaderLabels(hdr(False))

    def _show_ell(self):
        _sync_ell_fields(self)

    def _current_ell(self):
        return _current_ell_from(self)

    def _proj_group(self, title, w):
        g = QGroupBox(title)
        gl = QGridLayout(g)
        w["l0"] = QLineEdit("105.00000000")
        w["x0"] = QLineEdit("0.00000000")
        w["y0"] = QLineEdit("500.00000000")
        w["h0"] = QLineEdit("0.00000000")
        w["k"] = QLineEdit("1.0000000000")
        w["b0"] = QLineEdit("30.00000000")
        w["rig"] = QRadioButton("严密工程椭球")
        w["app"] = QRadioButton("近似工程椭球")
        w["app"].setChecked(True)
        w["b0"].setEnabled(False)
        for _k in ("l0", "x0", "y0", "h0", "k", "b0"):
            w[_k].setMaximumWidth(190)
        w["rig"].toggled.connect(w["b0"].setEnabled)
        rows = [("中央子午线(d.ms)", "l0"), ("加常数 x0(km)", "x0"), ("y0(km)", "y0"),
                ("投影面大地高(m)", "h0"), ("Utm k", "k"), ("平均纬度(d.ms)", "b0")]
        for i, (lab, key) in enumerate(rows):
            gl.addWidget(QLabel(lab), i, 0)
            gl.addWidget(w[key], i, 1)
        gl.addWidget(w["rig"], len(rows), 0)
        gl.addWidget(w["app"], len(rows), 1)
        gl.setColumnStretch(1, 1)
        return g

    def _show_ell(self):
        _sync_ell_fields(self)

    def _current_ell(self):
        return _current_ell_from(self)

    def _read_params(self, w):
        from app.core import anglefmt
        from app.core.gk_engine import ProjParams
        ell = self._current_ell()
        try:
            h0 = float(w["h0"].text())
            k = float(w["k"].text())
            x0 = float(w["x0"].text())
            y0 = float(w["y0"].text())
        except ValueError as e:
            raise ValueError(f"投影参数需为数值（h0/k/x0/y0）：{e}") from e
        return ProjParams(ell.a, ell.inv_f,
                          anglefmt.parse_flexible(w["l0"].text(), "dms"),
                          anglefmt.parse_flexible(w["b0"].text(), "dms"),
                          h0, k, x0, y0,
                          rigorous=w["rig"].isChecked())

    def import_pts(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择源数据", "",
                                              "数据文件 (*.csv *.txt *.xlsx *.xlsm)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str):
        """通用导入：txt/csv/xlsx，分隔符、表头、点号列自动识别（xy 两坐标列）。"""
        from app.common.io_import import read_points_table
        try:
            rows, notes = read_points_table(path, coord_cols=2, keep_extra=True)
        except Exception as e:  # noqa: BLE001
            _quiet_warn(self, "导入失败", str(e))
            return
        if notes:
            self.table_in.setToolTip("；".join(notes))
        self.table_in.setRowCount(0)
        for i, (name, vals) in enumerate(rows):
            r = self.table_in.rowCount()
            self.table_in.insertRow(r)
            self.table_in.setItem(r, 0, QTableWidgetItem(name or f"P{i+1}"))
            for j, v in enumerate(vals[:3]):
                self.table_in.setItem(r, j + 1, QTableWidgetItem(str(v)))
        self.table_in.insertRow(self.table_in.rowCount())  # 末尾留空行便于续输
        self.table_out.setRowCount(0)
        self._result_rows = []

    def clear_rows(self):
        self.table_in.setRowCount(0)
        self.table_out.setRowCount(0)
        self.table_in.insertRow(0)  # 保留一行空行供直接键入
        self._result_rows = []

    def _auto_rerun(self):
        """类型/格式切换时自动重算（有输入数据才跑；自动模式警告静默）。"""
        has = any(self.table_in.item(r, c) and self.table_in.item(r, c).text().strip()
                  for r in range(self.table_in.rowCount()) for c in (1, 2))
        if not has:
            return
        self._auto = True
        try:
            self.run()
        finally:
            self._auto = False

    def run(self):
        import math
        from app.core import anglefmt
        try:
            src = self._read_params(self.src_widgets)
            dst = self._read_params(self.dst_widgets)
        except Exception as e:  # noqa: BLE001
            if not self._auto:
                _quiet_warn(self, "参数有误", str(e))
            return
        src_bl = self.rb_src_bl.isChecked()
        dst_bl = self.rb_dst_bl.isChecked()
        src_dms = self.fmt_src_dms.isChecked()
        dst_dms = self.fmt_dst_dms.isChecked()
        self.table_out.setRowCount(0)
        self._result_rows = []
        for r in range(self.table_in.rowCount()):
            def cell(c, r=r):
                it = self.table_in.item(r, c)
                return it.text().strip() if it else ""
            name = cell(0) or f"P{r+1}"
            v1, v2, vh = cell(1), cell(2), cell(3)
            if not v1 or not v2:
                continue
            try:
                if src_bl:
                    if src_dms:
                        b = anglefmt.parse_flexible(v1, "dms")
                        l = anglefmt.parse_flexible(v2, "dms")
                    else:
                        b, l = float(v1) * math.pi / 180.0, float(v2) * math.pi / 180.0
                else:
                    b, l = src.inverse(float(v1), float(v2))
                if dst_bl:
                    if dst_dms:
                        o1, o2 = anglefmt.rad_to_dms(b), anglefmt.rad_to_dms(l)
                    else:
                        o1, o2 = b * 180.0 / math.pi, l * 180.0 / math.pi
                else:
                    o1, o2 = dst.forward(b, l)
            except Exception as e:  # noqa: BLE001
                if not self._auto:
                    _quiet_warn(self, "转换失败", f"{name}: {e}")
                return
            # 高程不参与计算，原值透传（有则输出，无则留空）
            h_out = ("%.4f" % float(vh)) if vh else ""
            rr = self.table_out.rowCount()
            self.table_out.insertRow(rr)
            self.table_out.setItem(rr, 0, QTableWidgetItem(name))
            fmt = ("%.8f" if dst_dms else "%.9f") if dst_bl else "%.4f"
            s1, s2 = fmt % o1, fmt % o2
            self.table_out.setItem(rr, 1, QTableWidgetItem(s1))
            self.table_out.setItem(rr, 2, QTableWidgetItem(s2))
            self.table_out.setItem(rr, 3, QTableWidgetItem(h_out))
            self._result_rows.append([name, s1, s2, h_out])
        _oplog().log("zone_transform", {"n": len(self._result_rows),
                                        "src": ("BL:" + ("d.ms" if src_dms else "d")) if src_bl else "xy",
                                        "dst": ("BL:" + ("d.ms" if dst_dms else "d")) if dst_bl else "xy",
                                        "rigorous": [src.rigorous, dst.rigorous], "h0": [src.h0, dst.h0]})
        self.table_out.resizeColumnsToContents()

    def save_result(self):
        if not self._result_rows:
            _quiet_info(self, "提示", "没有可保存的结果")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存结果", "换带结果.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("点号,值1,值2,高程\n")
            for row in self._result_rows:
                f.write(",".join([row[0]] + [("" if v is None else str(v)) for v in row[1:]]) + "\n")
        _quiet_info(self, "已保存", path)


# ============================================================ 空间直角/大地/高斯平面转换
class SpacePage(QWidget):
    """XYZ / BLH / xyH 七型互转（含 BLH 表现形式互转）。"""

    TYPES = [("BLH->BLH", "BLH → BLH（大地坐标 d.ms ↔ d 表现形式互转）"),
             ("XYZ->BLH", "XYZ → BLH（空间直角坐标 → 经纬度+大地高）"),
             ("XYZ->xyH", "XYZ → xyH（空间直角坐标 → 高斯平面+大地高）"),
             ("BLH->XYZ", "BLH → XYZ（经纬度+大地高 → 空间直角坐标）"),
             ("BLH->xyH", "BLH → xyH（经纬度+大地高 → 高斯平面）"),
             ("xyH->XYZ", "xyH → XYZ（高斯平面+大地高 → 空间直角坐标）"),
             ("xyH->BLH", "xyH → BLH（高斯平面+大地高 → 经纬度）")]

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("空间直角 · 大地坐标 · 高斯平面 转换", "XYZ/BLH/xyH 六型互转；角度支持 d.ms 编码与十进制度；"
                                                            "投影参数支持 UTM 尺度与工程椭球（严密/近似）；支持自定义椭球。"))
        top = QHBoxLayout()
        g0 = QGroupBox("椭球与投影参数")
        f0 = QGridLayout(g0)
        self.ell_combo = QComboBox()
        from app.core.ellipsoid import all_ellipsoids
        for nm in all_ellipsoids():
            self.ell_combo.addItem(nm)
        self.ell_combo.addItem("自定义")
        self.ell_combo.setCurrentText("CGCS2000")
        self.a_edit = QLineEdit("6378137.0000")
        self.invf_edit = QLineEdit("298.257223563")
        f0.addWidget(QLabel("椭球"), 0, 0)
        f0.addWidget(self.ell_combo, 0, 1, 1, 3)
        f0.addWidget(QLabel("a(m)"), 1, 0)
        f0.addWidget(self.a_edit, 1, 1)
        f0.addWidget(QLabel("1/f"), 1, 2)
        f0.addWidget(self.invf_edit, 1, 3)
        self.l0_edit = QLineEdit("105.00000000")
        self.x0_edit = QLineEdit("0.00000000")
        self.y0_edit = QLineEdit("500.00000000")
        self.h0_edit = QLineEdit("0.00000000")
        self.k_edit = QLineEdit("1.0000000000")
        self.b0_edit = QLineEdit("30.00000000"); self.b0_edit.setEnabled(False)
        for _w in (self.l0_edit, self.x0_edit, self.y0_edit, self.h0_edit,
                   self.k_edit, self.b0_edit):
            _w.setMaximumWidth(190)
        self.rb_rig = QRadioButton("严密工程椭球")
        self.rb_app = QRadioButton("近似工程椭球")
        self.rb_app.setChecked(True)
        self.rb_rig.toggled.connect(self.b0_edit.setEnabled)
        rows = [("中央子午线(d.ms)", self.l0_edit), ("加常数 x0(km)", self.x0_edit),
                ("y0(km)", self.y0_edit), ("投影面大地高(m)", self.h0_edit),
                ("Utm k", self.k_edit), ("平均纬度(d.ms)", self.b0_edit)]
        for i, (lab, wdt) in enumerate(rows):
            r, c = 2 + i // 2, (i % 2) * 2
            f0.addWidget(QLabel(lab), r, c)
            f0.addWidget(wdt, r, c + 1)
        f0.addWidget(self.rb_rig, 5, 0)
        f0.addWidget(self.rb_app, 5, 1)
        f0.setColumnStretch(1, 1)
        f0.setColumnStretch(3, 1)
        top.addWidget(g0, 5)
        g1 = QGroupBox("转换类型")
        gv1 = QVBoxLayout(g1)
        self.type_rb = {}
        for key, label in self.TYPES:
            rb = QRadioButton(label)
            self.type_rb[key] = rb
            gv1.addWidget(rb)
        self.type_rb["XYZ->BLH"].setChecked(True)
        top.addWidget(g1, 4)
        v.addLayout(top)
        # 独立容器隔离互斥域：输入/输出格式各自成组
        self.fmt_in_w = QWidget()
        fmt_row1 = QHBoxLayout(self.fmt_in_w)
        fmt_row1.setContentsMargins(0, 0, 0, 0)
        fmt_row1.addWidget(QLabel("输入角度格式"))
        self.fmt_in_dms = QRadioButton("d.ms 格式")
        self.fmt_in_d = QRadioButton("d 格式")
        self.fmt_in_dms.setChecked(True)
        for r_ in (self.fmt_in_dms, self.fmt_in_d):
            fmt_row1.addWidget(r_)
        fmt_row1.addStretch(1)
        self.fmt_out_w = QWidget()
        fmt_row2 = QHBoxLayout(self.fmt_out_w)
        fmt_row2.setContentsMargins(0, 0, 0, 0)
        fmt_row2.addWidget(QLabel("输出角度格式"))
        self.fmt_out_dms = QRadioButton("d.ms 格式")
        self.fmt_out_d = QRadioButton("d 格式")
        self.fmt_out_dms.setChecked(True)
        for r_ in (self.fmt_out_dms, self.fmt_out_d):
            fmt_row2.addWidget(r_)
        fmt_row2.addStretch(1)
        v.addWidget(self.fmt_in_w)
        v.addWidget(self.fmt_out_w)
        io = QHBoxLayout()
        g2 = QGroupBox("输入源坐标")
        gv2 = QVBoxLayout(g2)
        self.table_in = QTableWidget(0, 4)
        self.table_in.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv2.addWidget(self.table_in, 1)
        io.addWidget(g2, 1)
        g3 = QGroupBox("输出目标坐标")
        gv3 = QVBoxLayout(g3)
        self.table_out = QTableWidget(0, 4)
        self.table_out.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv3.addWidget(self.table_out, 1)
        io.addWidget(g3, 1)
        v.addLayout(io, 2)
        btns = QHBoxLayout()
        self.btn_import = QPushButton("读入源文件")
        self.btn_import.setProperty("class", "secondary")
        self.btn_clear = QPushButton("清除数据")
        self.btn_clear.setProperty("class", "secondary")
        self.btn_run = QPushButton("转换")
        self.btn_save = QPushButton("保存结果")
        self.btn_save.setProperty("class", "secondary")
        for b in (self.btn_import, self.btn_clear, self.btn_run, self.btn_save):
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        self.btn_import.clicked.connect(self.import_pts)
        self.btn_clear.clicked.connect(self.clear_rows)
        self.btn_run.clicked.connect(self.run)
        self.btn_save.clicked.connect(self.save_result)
        self.ell_combo.currentIndexChanged.connect(self._show_ell)
        for rb in self.type_rb.values():
            rb.toggled.connect(self._update_headers)
            rb.toggled.connect(self._auto_rerun)
        for rb in (self.fmt_in_dms, self.fmt_in_d, self.fmt_out_dms, self.fmt_out_d):
            rb.toggled.connect(self._update_headers)
            rb.toggled.connect(self._auto_rerun)
        self._auto = False
        self._enter_filter_in = _enable_enter_add(self.table_in)
        self.table_in.insertRow(0)  # 初始空行供直接键入
        self._show_ell()
        self._update_headers()
        self._result_rows = []

    def _show_ell(self):
        _sync_ell_fields(self)

    def _current_ell(self):
        return _current_ell_from(self)

    def _kind(self):
        for key, rb in self.type_rb.items():
            if rb.isChecked():
                return key
        return "XYZ->BLH"

    def _update_headers(self):
        kind = self._kind()
        s, _, d = kind.partition("->")
        u_in = "d.ms" if self.fmt_in_dms.isChecked() else "d"
        u_out = "d.ms" if self.fmt_out_dms.isChecked() else "d"
        col_in, col_out = _spt.column_headers(kind, u_in, u_out)
        self.table_in.setHorizontalHeaderLabels(["点号"] + col_in)
        self.table_out.setHorizontalHeaderLabels(["点号"] + col_out)
        # 角度格式选项仅在涉及经纬度的一侧出现
        self.fmt_in_w.setVisible(s == "BLH")
        self.fmt_out_w.setVisible(d == "BLH")

    def _auto_rerun(self):
        """类型/格式切换时自动重算（有输入数据才跑；自动模式警告静默）。"""
        has = any(self.table_in.item(r, c) and self.table_in.item(r, c).text().strip()
                  for r in range(self.table_in.rowCount()) for c in (1, 2))
        if not has:
            return
        self._auto = True
        try:
            self.run()
        finally:
            self._auto = False

    def _proj(self):
        from app.core import anglefmt
        from app.core.gk_engine import ProjParams
        ell = self._current_ell()
        try:
            h0 = float(self.h0_edit.text())
            k = float(self.k_edit.text())
            x0 = float(self.x0_edit.text())
            y0 = float(self.y0_edit.text())
        except ValueError as e:
            raise ValueError(f"投影参数需为数值（h0/k/x0/y0）：{e}") from e
        return ProjParams(ell.a, ell.inv_f,
                          anglefmt.parse_flexible(self.l0_edit.text(), "dms"),
                          anglefmt.parse_flexible(self.b0_edit.text(), "dms"),
                          h0, k, x0, y0,
                          rigorous=self.rb_rig.isChecked())

    def import_pts(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择源数据", "",
                                              "数据文件 (*.csv *.txt *.xlsx *.xlsm)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str):
        """导入：手簿 .cot（按参数侧别取 BLH 或 xyh）/ CASS 公共点 / 通用表格（三坐标列）。"""
        from app.common import pair_io
        expect_lonlat = self._src_lonlat()
        filled = None
        if Path(path).suffix.lower() == ".cot":
            try:
                pts = pair_io.parse_cot(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            filled = [([p.l_deg, p.b_deg, p.h_ell], p.name) for p in pts] if expect_lonlat                 else [([p.x, p.y, p.h_normal], p.name) for p in pts]
        elif pair_io.looks_like_cass(path):
            try:
                pairs = pair_io.parse_cass(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            filled = [([p.x1, p.y1, p.h1], f"CASS{i+1}") for i, p in enumerate(pairs)]
        if filled is None:
            try:
                rows, notes = read_points_table(path, coord_cols=3, allow_pad=True)
                filled = [(vals, name) for name, vals in rows]
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            if notes:
                self.table_in.setToolTip("；".join(notes))
        self.table_in.setRowCount(0)
        for i, (vals, name) in enumerate(filled):
            r = self.table_in.rowCount()
            self.table_in.insertRow(r)
            self.table_in.setItem(r, 0, QTableWidgetItem(name or f"P{i+1}"))
            for j, v in enumerate(vals):
                self.table_in.setItem(r, j + 1, QTableWidgetItem(str(v)))
        self.table_in.insertRow(self.table_in.rowCount())  # 末尾留空行便于续输
        self.table_out.setRowCount(0)
        self._result_rows = []

    def clear_rows(self):
        self.table_in.setRowCount(0)
        self.table_out.setRowCount(0)
        self.table_in.insertRow(0)  # 保留一行空行供直接键入
        self._result_rows = []

    def run(self):
        kind = self._kind()
        src_t, _, dst_t = kind.partition("->")
        ell = self._current_ell()
        pj = self._proj()
        self.table_out.setRowCount(0)
        self._result_rows = []
        pts = []
        for r in range(self.table_in.rowCount()):
            cells = [self.table_in.item(r, c).text().strip() if self.table_in.item(r, c) else ""
                     for c in range(4)]
            if not (cells[1] and cells[2] and cells[3]):
                continue
            pts.append((cells[0] or f"P{r+1}", cells[1], cells[2], cells[3]))
        results, err = _spt.convert(pts, src_t, dst_t, ell, pj,
                                    self.fmt_in_dms.isChecked(), self.fmt_out_dms.isChecked())
        for res in results:
            rr = self.table_out.rowCount()
            self.table_out.insertRow(rr)
            self.table_out.setItem(rr, 0, QTableWidgetItem(res["name"]))
            for j, val in enumerate(res["vals"]):
                self.table_out.setItem(rr, j + 1, QTableWidgetItem(res["fmts"][j] % val))
            self._result_rows.append([res["name"]] + [res["fmts"][j] % val
                                                      for j, val in enumerate(res["vals"])])
        if err and not self._auto:
            _quiet_warn(self, "转换失败", f"{err[0]}: {err[1]}")
            return
        _oplog().log("space_transform", {"n": len(self._result_rows), "type": kind,
                                         "rigorous": pj.rigorous, "h0": pj.h0})
        self.table_out.resizeColumnsToContents()

    def save_result(self):
        if not self._result_rows:
            _quiet_info(self, "提示", "没有可保存的结果")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存结果", "空间转换结果.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("点号,值1,值2,值3\n")
            for row in self._result_rows:
                f.write(",".join([row[0]] + [("" if v is None else str(v)) for v in row[1:]]) + "\n")
        _quiet_info(self, "已保存", path)


# ============================================================ 参数应用 · 坐标批量转换
class ApplyPage(QWidget):
    """用参数库中已保存的参数对坐标列表做批量转换。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.addWidget(_title("参数应用 · 坐标批量转换", "选择参数库中已保存的转换参数（四参数/三参数/七参数/链式），"
                                                    "批量转换坐标列表。角度输入输出均为 d.ms 编码。"))
        mid = QHBoxLayout()
        # 参数选择
        g0 = QGroupBox("1. 选择参数")
        gv0 = QVBoxLayout(g0)
        r0 = QHBoxLayout()
        self.param_combo = QComboBox()
        self.btn_reload = QPushButton("刷新参数库")
        self.btn_reload.setProperty("class", "secondary")
        self.btn_open_dir = QPushButton("打开参数文件夹")
        self.btn_open_dir.setProperty("class", "secondary")
        self.btn_del_param = QPushButton("删除此参数")
        self.btn_del_param.setProperty("class", "secondary")
        r0.addWidget(self.param_combo, 1)
        r0.addWidget(self.btn_reload)
        r0.addWidget(self.btn_open_dir)
        r0.addWidget(self.btn_del_param)
        gv0.addLayout(r0)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(110)
        gv0.addWidget(self.detail)
        mid.addWidget(g0, 1)
        v.addLayout(mid)
        self.lbl_dir = QLabel("转换方向：-")
        self.lbl_dir.setObjectName("muted")
        v.addWidget(self.lbl_dir)
        # 大地坐标角度格式：自动识别（分/秒 ≥60 结构判定）或手动指定
        self.fmt_w = QWidget()
        fmt_row = QHBoxLayout(self.fmt_w)
        fmt_row.setContentsMargins(0, 0, 0, 0)
        fmt_row.addWidget(QLabel("角度格式（大地坐标）"))
        self.fmt_auto = QRadioButton("自动识别")
        self.fmt_dms = QRadioButton("d.ms 格式")
        self.fmt_d = QRadioButton("d 格式")
        self.fmt_auto.setChecked(True)
        for r_ in (self.fmt_auto, self.fmt_dms, self.fmt_d):
            fmt_row.addWidget(r_)
        fmt_row.addStretch(1)
        v.addWidget(self.fmt_w)
        self.trans_w = QWidget()
        trans_row = QHBoxLayout(self.trans_w)
        trans_row.setContentsMargins(0, 0, 0, 0)
        trans_row.addWidget(QLabel("转换内容"))
        self.chk_do_plane = QCheckBox("转换平面坐标")
        self.chk_do_height = QCheckBox("转换高程")
        trans_row.addWidget(self.chk_do_plane)
        trans_row.addWidget(self.chk_do_height)
        trans_row.addStretch(1)
        v.addWidget(self.trans_w)
        io = QHBoxLayout()
        g2 = QGroupBox("2. 输入坐标（可从 CSV/TXT 读入）")
        gv2 = QVBoxLayout(g2)
        self.table_in = QTableWidget(0, 4)
        self.table_in.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv2.addWidget(self.table_in, 1)
        io.addWidget(g2, 1)
        g3 = QGroupBox("3. 转换结果")
        gv3 = QVBoxLayout(g3)
        self.table_out = QTableWidget(0, 4)
        self.table_out.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        gv3.addWidget(self.table_out, 1)
        io.addWidget(g3, 1)
        v.addLayout(io, 2)
        btns = QHBoxLayout()
        self.btn_import = QPushButton("读入源文件")
        self.btn_import.setProperty("class", "secondary")
        self.btn_clear = QPushButton("清除数据")
        self.btn_clear.setProperty("class", "secondary")
        self.btn_run = QPushButton("转换")
        self.btn_save = QPushButton("保存结果")
        self.btn_save.setProperty("class", "secondary")
        for b in (self.btn_import, self.btn_clear, self.btn_run, self.btn_save):
            btns.addWidget(b)
        btns.addStretch(1)
        v.addLayout(btns)
        self.btn_reload.clicked.connect(self.reload_params)
        self.btn_open_dir.clicked.connect(self.open_param_dir)
        self.btn_del_param.clicked.connect(self.delete_param)
        self.param_combo.currentIndexChanged.connect(self._show_detail)
        self.btn_import.clicked.connect(self.import_pts)
        self.btn_clear.clicked.connect(self.clear_rows)
        self.btn_run.clicked.connect(self.run)
        self.btn_save.clicked.connect(self.save_result)
        self._enter_filter_in = _enable_enter_add(self.table_in)
        self.table_in.insertRow(0)  # 初始空行供直接键入
        self._quiet = False
        self.reload_params()
        self._result_rows = []
        self.param_combo.setToolTip("选择参数库中的转换参数；下方文本框显示方法代号、精度与适用性说明")
        for _w, _t in ((self.fmt_auto, "自动识别：分/秒位≥60 时判定为十进制度，否则按 d.ms 解码（软件默认口径）"),
                       (self.fmt_dms, "按 d.ms 编码解码：105.302568 = 105°30′25.68″"),
                       (self.fmt_d, "按十进制度：105.5 = 105°30′")):
            _w.setToolTip(_t)
        self.chk_do_plane.setToolTip("勾选=应用参数中的平面转换；不勾=平面原值透传")
        self.chk_do_height.setToolTip("勾选=应用参数中的高程转换（拟合/格网）；不勾=高程原值透传")

    # ---- 参数 ----
    def open_param_dir(self):
        """在资源管理器中打开参数存放文件夹（params/）。"""
        PARAMS_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(PARAMS_DIR))

    def delete_param(self):
        """删除当前选中的参数（删除前弹确认框）。"""
        name = self.param_combo.currentText()
        if not name:
            QMessageBox.information(self, "提示", "请先选择要删除的参数")
            return
        r = QMessageBox.question(self, "删除参数",
                                 f"确认删除参数「{name}」？删除后不可恢复。",
                                 QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if r != QMessageBox.Yes:
            return
        try:
            ParamLibrary(PARAMS_DIR).delete(name)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(e))
            return
        self.reload_params()
        self.detail.setPlainText(f"已删除参数：{name}")

    def reload_params(self):
        self.param_combo.blockSignals(True)
        self.param_combo.clear()
        lib = ParamLibrary(PARAMS_DIR)
        for p in lib.list():
            self.param_combo.addItem(p["name"], p["file"])
        self.param_combo.blockSignals(False)
        if self.param_combo.count():
            self._show_detail()

    def _load_param(self):
        name = self.param_combo.currentText()
        if not name:
            return None
        try:
            return ParamLibrary(PARAMS_DIR).load(name)
        except Exception:  # noqa: BLE001
            return None

    def _show_detail(self):
        d = self._load_param()
        if not d:
            self.detail.setPlainText("（无参数）")
            self.lbl_dir.setText("转换方向：-")
            return
        acc = d.get("accuracy", {}) or {}
        lines = [f"类型：{d.get('kind')}    椭球：{d.get('ellipsoid')}    "
                 f"点数：{acc.get('n_points', '?')}    RMS平面：{acc.get('rms_xy_m')}",
                 f"RMS高程：{acc.get('rms_h_m')}    LOO高程：{acc.get('loo_rms_h_m')}    "
                 f"备注：{acc.get('note', '')}"]
        if d.get("planar4"):
            p4 = d["planar4"]
            lines.append(f"四参数：dx={p4['dx']:.4f} dy={p4['dy']:.4f} a={p4['a']:.9f} b={p4['b']:.9f}")
        if d.get("three"):
            p3 = d["three"]
            lines.append(f"三参数：dX={p3['dx']:.4f} dY={p3['dy']:.4f} dZ={p3['dz']:.4f}")
        if d.get("seven"):
            p7 = d["seven"]
            lines.append(f"七参数：dX={p7['dx']:.4f} dY={p7['dy']:.4f} dZ={p7['dz']:.4f} "
                         f"Rx={p7['rx_s']}″ Ry={p7['ry_s']}″ Rz={p7['rz_s']}″ m={p7['scale_ppm']}ppm")
        if d.get("projection"):
            pj = d["projection"]
            lines.append(f"投影：L0={pj.get('l0')} x0={pj.get('x0')} y0={pj.get('y0')} k={pj.get('k')}")
        if d.get("projection_src"):
            ps = d["projection_src"]
            ztxt = (f"（{ps.get('band')} 带号{ps.get('zone')}，自动剥离）"
                    if ps.get("zone") else "")
            lines.append(f"源投影：L0={ps.get('l0')} x0={ps.get('x0')} y0={ps.get('y0')} "
                         f"h0={ps.get('h0')}{ztxt}")
        if d.get("heightfit"):
            hf = d["heightfit"]
            lines.append(f"高程拟合：{hf.get('mode')}({hf.get('value_type')}) LOO={hf.get('loo_rms')}")
        if d.get("poly"):
            pl = d["poly"]
            lines.append(f"多项式：{pl.get('kind')} {pl.get('degree')} 阶（重心化）    "
                         f"内符合RMS={pl.get('rms')}    最大残差={pl.get('max_err')}")
        if d.get("accuracy", {}).get("note"):
            lines.append(f"说明：{d['accuracy']['note']}")
        self.detail.setPlainText("\n".join(str(x) for x in lines))
        sk = d.get("source_kind") or "?"
        tk = d.get("target_kind") or ("planar" if d.get("planar4") or d.get("projection") else "?")
        self.lbl_dir.setText(f"转换方向：源={sk} → 目标={tk}   "
                             f"（lonlat：B/L 用 d.ms 编码；planar：x北 y东）")

    # ---- 表格 ----
    def _sync_transform_checks(self):
        """按参数类型与输入数据联动 转换平面/转换高程 勾选与灰显。"""
        d = self._load_param()
        kind = (d or {}).get("kind")
        if kind in ("seven", "three", "seven2d"):
            for w_ in (self.chk_do_plane, self.chk_do_height):
                w_.setEnabled(False)
                w_.setChecked(True)  # 七参数三维模型不可拆分
        else:
            for w_ in (self.chk_do_plane, self.chk_do_height):
                w_.setEnabled(True)
            self.chk_do_plane.setChecked(False)
            self.chk_do_height.setChecked(False)
        # 输入无高程列 → 转换高程灰显
        has_h = any(self.table_in.item(r, 3) and self.table_in.item(r, 3).text().strip()
                    for r in range(self.table_in.rowCount()))
        if not has_h and kind not in ("seven", "three", "seven2d"):
            self.chk_do_height.setEnabled(False)
            self.chk_do_height.setChecked(False)
        # 角度格式行仅在大地坐标源时显示
        self.fmt_w.setVisible(self._src_lonlat())

    def _src_lonlat(self, detected=None):
        d = self._load_param()
        if detected is not None:
            return detected == "lonlat"
        return ((d or {}).get("source_kind") == "lonlat"
                or (d or {}).get("kind") in ("seven", "three", "chain74"))

    def _headers(self, detected=None):
        lonlat = self._src_lonlat(detected)
        if lonlat:
            u = "d" if self.fmt_d.isChecked() else "d.ms"
            return ["点号", f"B({u})", f"L({u})", "H(m)"]
        return ["点号", "x(m)", "y(m)", "h(m)"]

    def import_pts(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择源数据", "",
                                              "数据文件 (*.csv *.txt *.xlsx *.xlsm)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path: str):
        """导入：手簿 .cot（按参数侧别取 BLH 或 xyh）/ CASS 公共点 / 通用表格（三坐标列）。"""
        from app.common import pair_io
        expect_lonlat = self._src_lonlat()
        filled = None
        if Path(path).suffix.lower() == ".cot":
            try:
                pts = pair_io.parse_cot(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            filled = [([p.l_deg, p.b_deg, p.h_ell], p.name) for p in pts] if expect_lonlat                 else [([p.x, p.y, p.h_normal], p.name) for p in pts]
        elif pair_io.looks_like_cass(path):
            try:
                pairs = pair_io.parse_cass(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            filled = [([p.x1, p.y1, p.h1], f"CASS{i+1}") for i, p in enumerate(pairs)]
        elif pair_io.looks_like_south_pairs(path):
            try:
                sp = pair_io.parse_south_pairs(path)
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            filled = [([p.x1, p.y1, p.h1], p.name) for p in sp]
        if filled is None:
            try:
                rows, notes = read_points_table(path, coord_cols=3, allow_pad=True)
                filled = [(vals, name) for name, vals in rows]
            except Exception as e:  # noqa: BLE001
                _quiet_warn(self, "导入失败", str(e))
                return
            if notes:
                self.table_in.setToolTip("；".join(notes))
        self.table_in.setRowCount(0)
        for i, (vals, name) in enumerate(filled):
            r = self.table_in.rowCount()
            self.table_in.insertRow(r)
            self.table_in.setItem(r, 0, QTableWidgetItem(name or f"P{i+1}"))
            for j, v in enumerate(vals):
                self.table_in.setItem(r, j + 1, QTableWidgetItem(str(v)))
        self.table_in.insertRow(self.table_in.rowCount())  # 末尾留空行便于续输
        self.table_out.setRowCount(0)
        self._result_rows = []
        self._detect_and_label()

    def _read_input_rows(self):
        """读输入表 → (names, (n,3) 数组)；忽略空行。"""
        import numpy as np
        names, vals = [], []
        for r in range(self.table_in.rowCount()):
            cells = []
            for c in range(4):
                it = self.table_in.item(r, c)
                cells.append(it.text().strip() if it else "")
            if not (cells[1] and cells[2] and cells[3]):
                continue
            names.append(cells[0] or f"P{r+1}")
            try:
                vals.append([float(cells[1]), float(cells[2]), float(cells[3])])
            except ValueError:
                raise ValueError(f"第 {r+1} 行坐标不是数值：{cells[1]} {cells[2]} {cells[3]}")
        if not vals:
            raise ValueError("无有效输入行")
        return names, np.asarray(vals, dtype=float)

    def _detect_and_label(self):
        """识别输入坐标类型并联动表头/标签。"""
        import numpy as np
        from app.core.autoselect import detect_coord_kind
        vals = []
        for r in range(self.table_in.rowCount()):
            cells = [self.table_in.item(r, c).text().strip() if self.table_in.item(r, c) else ""
                     for c in range(1, 4)]
            try:
                if cells[0] and cells[1]:
                    vals.append([float(cells[0]), float(cells[1])])
            except ValueError:
                continue
        if not vals:
            return
        kind = detect_coord_kind(np.asarray(vals))
        lonlat = kind == "lonlat"
        self.table_in.setHorizontalHeaderLabels(self._headers(kind))
        d = self._load_param()
        expect = self._src_lonlat()
        tip = ""
        if d and lonlat != expect:
            tip = "  ⚠ 与参数期望的源类型不一致，转换将被拒绝"
        self.lbl_dir.setText(self.lbl_dir.text().split("  ⚠")[0] + tip)

        self._sync_transform_checks()
    def clear_rows(self):
        self.table_in.setRowCount(0)
        self.table_out.setRowCount(0)
        self.table_in.insertRow(0)  # 保留一行空行供直接键入
        self._result_rows = []

    # ---- 转换 ----
    def run(self):
        from app.core import anglefmt
        from app.core.autoselect import detect_coord_kind
        from app.core.ellipsoid import ELLIPSOIDS, geodetic_to_ecef, ecef_to_geodetic
        from app.core.transform3d import Param7, Param3, apply_7param, apply_3param
        from app.common.params import ParamApplier

        d = self._load_param()
        if not d:
            _quiet_info(self, "提示", "参数库为空，请先在“控制点转换”页计算并保存参数")
            return
        kind = d.get("kind")
        from app.core.ellipsoid import get_ellipsoid
        ell = get_ellipsoid(d.get("ellipsoid", "CGCS2000"))
        src_lonlat = d.get("source_kind") == "lonlat" or kind in ("seven", "three", "chain74")
        out_lonlat = kind in ("seven", "three") or kind in ("inv_direct", "inv_chain3", "inv_chain")

        # 1) 读取输入并自动识别坐标类型（平面 / 大地）
        names, vals_in = [], []
        for r in range(self.table_in.rowCount()):
            cells = []
            for c in range(4):
                it = self.table_in.item(r, c)
                cells.append(it.text().strip() if it else "")
            if not (cells[1] and cells[2]):
                continue
            try:
                v = [float(cells[1]), float(cells[2]),
                     float(cells[3]) if cells[3] else 0.0]
            except ValueError:
                _quiet_warn(self, "输入有误", f"第 {r+1} 行坐标不是数值")
                return
            names.append(cells[0] or f"P{r+1}")
            vals_in.append(v)
        if not vals_in:
            _quiet_info(self, "提示", "无有效输入行")
            return
        v12 = np_column_stack(vals_in)
        kind_det = detect_coord_kind(v12[:, :2])
        expect_lonlat = src_lonlat
        # 多项式参数且配了源侧投影：平面输入自动反算为大地（模型空间=大地），放行
        poly_inv_ok = (kind in ("poly2d", "poly3d") and expect_lonlat
                       and (d.get("projection_src") or {}).get("l0") is not None
                       and kind_det == "planar")
        if (kind_det == "lonlat") != expect_lonlat and not poly_inv_ok:
            _quiet_warn(self, "坐标类型不匹配",
                                f"输入坐标识别为 {kind_det}，但参数期望源为 "
                                f"{'大地坐标' if expect_lonlat else '平面坐标'}。请核对参数或数据。")
            return
        self.table_in.setHorizontalHeaderLabels(self._headers(kind_det))

        # 2) 角度归一化：大地源输入统一转十进制度再进引擎
        #    （自动识别：分/秒 ≥60 结构判定不可能是 d.ms → 十进制度；软件口径默认 d.ms）
        if expect_lonlat:
            def ang_deg(v):
                if self.fmt_dms.isChecked():
                    return anglefmt.dms_to_deg(v)
                if self.fmt_auto.isChecked() and anglefmt.dms_plausible(v):
                    return anglefmt.dms_to_deg(v)
                return v
            vals_in = [[ang_deg(v[0]), ang_deg(v[1]), v[2]] for v in vals_in]

        self.table_out.setRowCount(0)
        self._result_rows = []
        do_plane = self.chk_do_plane.isChecked()
        do_height = self.chk_do_height.isChecked()
        results, err = _apl.apply_points(names, vals_in, d, out_lonlat, src_lonlat,
                                         kind, do_plane, do_height, ell)
        for res in results:
            rr = self.table_out.rowCount()
            self.table_out.insertRow(rr)
            self.table_out.setItem(rr, 0, QTableWidgetItem(res["name"]))
            for j, val in enumerate(res["vals"]):
                self.table_out.setItem(rr, j + 1, QTableWidgetItem(res["fmts"][j] % val))
            self._result_rows.append([res["name"]] + [res["fmts"][j] % val
                                                      for j, val in enumerate(res["vals"])])
        if err:
            _quiet_warn(self, "转换失败", f"{err[0]}: {err[1]}")
            return
        _oplog().log("apply_params", {"n": len(self._result_rows), "kind": kind,
                                      "param": self.param_combo.currentText(),
                                      "detected": kind_det})
        self.table_out.resizeColumnsToContents()
    def save_result(self):
        if not self._result_rows:
            _quiet_info(self, "提示", "没有可保存的结果")
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存结果", "批量转换结果.csv", "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("点号,值1,值2,值3\n")
            for row in self._result_rows:
                f.write(",".join([row[0]] + [("" if v is None else str(v)) for v in row[1:]]) + "\n")
        _quiet_info(self, "已保存", path)
