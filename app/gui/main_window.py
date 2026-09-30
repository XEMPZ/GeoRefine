"""主窗口：左侧导航 + 页面栈。

正规入口：项目根目录 `python main.py`；本文件也可直接运行：
`python app/gui/main_window.py`（自动把项目根加进 sys.path）。
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # 直接以脚本运行时自举项目根
    _ROOT = Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QStackedWidget, QVBoxLayout, QWidget)

from app import APP_TITLE
from app.gui.pages import (AccuracyPage, ApplyPage, CadPage, ControlPointPage, GaussPage, HelpPage,
                           OsgbPage, LasPage,
                           HomePage, LedgerPage, ParamLibraryPage, PhotoPosPage, PolyPage, SpacePage,
                           _ROOT)
from app.gui.styles import ACCENT, BG, INK, MUTED, apply_qss

NAV = ["首页", "高斯投影换带", "空间坐标转换", "参数应用转换", "控制点转换", "照片POS处理",
       "OSGB模型转换", "点云模型转换", "多项式转换", "文件转换", "精度对比", "参数库",
       "处理台账", "关于本软件"]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(max(1240, self.minimumWidth()), 780)   # 默认≥最窄；1080p/4K@200% 均不塞满
        apply_qss(self)   # 样式表含指示器图片资源，须在 QApplication 就绪后调用
        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        # 左侧导航
        nav_panel = QFrame()
        nav_panel.setFixedWidth(190)
        nav_panel.setStyleSheet(f"QFrame {{ background: {BG}; border-right: 1px solid #d8d2c4; }}")
        nv = QVBoxLayout(nav_panel)
        nv.setContentsMargins(16, 20, 16, 16)
        brand = QLabel("GeoRefine")
        brand.setStyleSheet(f"font-size:16pt; font-weight:bold; color:{ACCENT}; background:transparent;")
        sub = QLabel("水准精化 · 坐标转换")
        sub.setStyleSheet(f"font-size:8pt; color:{MUTED}; background:transparent;")
        nv.addWidget(brand)
        nv.addWidget(sub)
        nv.addSpacing(12)
        self.nav = QListWidget()
        self.nav.addItems(NAV)
        self.nav.setFrameShape(QListWidget.NoFrame)
        self.nav.setCurrentRow(0)
        nv.addWidget(self.nav, 1)
        ver = QLabel("v1.1")
        ver.setStyleSheet(f"color:{MUTED}; background:transparent; font-size:8pt;")
        nv.addWidget(ver)
        h.addWidget(nav_panel)
        # 页面栈（套滚动区：窗口再小也只出滚动条，不抬高最小尺寸）
        self.stack = QStackedWidget()
        self.pages = {
            0: HomePage(), 1: GaussPage(), 2: SpacePage(), 3: ApplyPage(),
            4: ControlPointPage(), 5: PhotoPosPage(), 6: OsgbPage(),
            7: LasPage(), 8: PolyPage(), 9: CadPage(), 10: AccuracyPage(),
            11: ParamLibraryPage(), 12: LedgerPage(), 13: HelpPage(),
        }
        # 竖向滑块仅在控制点页内部（该页内容多）；其余页整页完整显示，
        # 滑块只出现在各数据控件（表格/列表）内部
        from PySide6.QtWidgets import QScrollArea
        cp_host = QScrollArea()
        cp_host.setWidgetResizable(True)
        cp_host.setFrameShape(QFrame.NoFrame)
        cp_host.setWidget(self.pages[4])
        for i in range(len(NAV)):
            self.stack.addWidget(cp_host if i == 4 else self.pages[i])
        natural = max(self.pages[i].minimumSizeHint().width() for i in range(len(NAV)) if i != 4)
        self.stack.setMinimumWidth(natural)
        h.addWidget(self.stack, 1)
        self.setMinimumSize(190 + natural + 30, 640)
        self.setCentralWidget(central)
        self.nav.currentRowChanged.connect(self._switch)
        self.statusBar().showMessage(f"工作目录：{_ROOT}")

    def _switch(self, row: int):
        self.stack.setCurrentIndex(row)
        page = self.pages.get(row)
        if hasattr(page, "reload_models"):
            page.reload_models()
        if hasattr(page, "reload_params"):
            page.reload_params()
        if isinstance(page, HomePage):
            page.refresh_status()

    def closeEvent(self, ev):  # noqa: N802
        for page in self.pages.values():
            for attr in ("worker", "scan_worker", "rollback_worker"):
                w = getattr(page, attr, None)
                if w is not None and w.isRunning():
                    if hasattr(w, "cancel"):
                        w.cancel()
                    w.wait(3000)
        super().closeEvent(ev)


if __name__ == "__main__":
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv)
    app.setApplicationName("GeoRefine")
    app.setStyle("Fusion")
    try:
        app.setFont(QFont("Microsoft YaHei UI", 9))
    except Exception:  # noqa: BLE001
        pass
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
