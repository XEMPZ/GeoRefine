"""界面样式 v3（Stamen 地图学风格）+ 指示器图标资源方案。

关键点：样式表中只要存在匹配 QCheckBox/QRadioButton 的规则，Qt 就用样式表绘制指示器，
原生对勾消失；纯 QSS 又画不出对勾 → 启动时生成 4 张指示器 PNG（矩形框+对勾 / 圆形框+圆点），
QSS 以 image: url(绝对路径) 引用。

使用：主窗口 `apply_qss(self)`（QApplication 就绪后调用）；
不要直接使用 QSS 常量做 setStyleSheet（指示器路径是占位符）。
"""
from __future__ import annotations

from pathlib import Path

from app.gui.indicator_assets import ensure_indicator_assets as _ensure_assets

BG = "#f4f1ea"          # 纸色背景
PANEL = "#ffffff"
BORDER = "#d8d2c4"
INK = "#1e2a32"
MUTED = "#6b7a83"
ACCENT = "#2a6f8e"      # 水蓝
ACCENT2 = "#7a8b6f"     # 地形绿
WARN = "#c96f4a"        # 等高线橙
ROW_ALT = "#f7f4ee"
HEADBG = "#3d4d57"      # 表头深（墨蓝灰）


def apply_qss(widget) -> None:
    """生成指示器资源并把最终样式表应用到 widget（QApplication 就绪后调用）。"""
    ind = _ensure_assets()
    qss = QSS.replace("{IND_CHK_ON}", Path(ind["chk_on"]).as_posix())
    qss = qss.replace("{IND_RAD_ON}", Path(ind["rad_on"]).as_posix())
    widget.setStyleSheet(qss)


QSS = f"""
* {{ font-family: "Microsoft YaHei UI"; font-size: 9pt; color: {INK}; }}

/* ---- 背景：只有窗体与 GroupBox 有实底，其余控件一律透明（消除文字白框） ---- */
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ background: transparent; }}
QGroupBox {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px;
  margin-top: 11px; padding: 6px 8px 6px 8px; font-weight: bold;
}}
QGroupBox::title {{
  subcontrol-origin: margin; left: 8px; color: {ACCENT};
  background: {BG}; padding: 0 3px;
}}
QLabel {{ background: transparent; color: {INK}; }}
QLabel#pageTitle {{ font-size: 14pt; font-weight: bold; background: transparent; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}

/* ---- 按钮 ---- */
QPushButton {{
  background: {ACCENT}; color: white; border: none; border-radius: 4px;
  padding: 4px 12px; min-height: 16px;
}}
QPushButton:hover {{ background: #35809f; }}
QPushButton:pressed {{ background: #225a75; }}
QPushButton:disabled {{ background: #b9c4c9; color: #e8ecef; }}
QPushButton:default {{ border: 2px solid #225a75; }}
QPushButton.secondary {{ background: {ACCENT2}; }}
QPushButton.secondary:hover {{ background: #6a7d5e; }}
QPushButton.secondary:pressed {{ background: #55664b; }}
QPushButton.warn {{ background: {WARN}; }}

/* ---- 输入控件 ---- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
  padding: 2px 5px; min-height: 16px; min-width: 24px;
  selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {ACCENT}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
  background: #f0ede6; color: {MUTED};
}}
QComboBox::drop-down {{ border: none; width: 16px; }}
QComboBox QAbstractItemView {{
  background: {PANEL}; border: 1px solid {BORDER};
  selection-background-color: {ACCENT}; selection-color: white;
  alternate-background-color: {ROW_ALT};
}}
QComboBox QAbstractItemView::item {{ padding: 3px 6px; min-height: 18px; }}

/* ---- 指示器（image 资源由 indicator_assets 生成：矩形框+对勾/圆形框+圆点） ---- */
QCheckBox, QRadioButton {{ background: transparent; spacing: 5px; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {MUTED}; }}
QCheckBox::indicator, QRadioButton::indicator {{
  width: 14px; height: 14px; border: 1px solid {BORDER};
  background: {PANEL}; border-radius: 2px;
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border: 1px solid {ACCENT}; }}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
  background: #ece9e2; border-color: {BORDER};
}}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox::indicator:checked {{ image: url({{IND_CHK_ON}}); border: 1px solid {ACCENT}; }}
QRadioButton::indicator:checked {{
  image: url({{IND_RAD_ON}}); border: 1px solid {ACCENT}; border-radius: 7px;
}}

/* ---- 表格（深表头 + 交替行 + 蓝选区，层次分明） ---- */
QTableWidget, QTableView {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
  gridline-color: #e7e2d7; alternate-background-color: {ROW_ALT};
  selection-background-color: #cfe2ec; selection-color: {INK};
}}
QTableWidget::item, QTableView::item {{ padding: 2px 6px; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: #cfe2ec; color: {INK}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
  background: {HEADBG}; color: white; border: none;
  border-right: 1px solid #55666f; padding: 4px 6px; font-weight: bold;
}}
QHeaderView::section:hover {{ background: #4a5b66; }}
QTableCornerButton::section {{ background: {HEADBG}; border: none; }}

/* ---- 列表/树 ---- */
QListWidget, QTreeWidget, QTreeView {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px; outline: 0;
}}
QListWidget::item, QTreeWidget::item {{ padding: 4px 6px; border-radius: 3px; }}
QListWidget::item:hover, QTreeWidget::item:hover {{ background: {ROW_ALT}; }}
QListWidget::item:selected, QTreeWidget::item:selected {{ background: {ACCENT}; color: white; }}

/* ---- 进度条 / 滚动条 ---- */
QProgressBar {{
  border: 1px solid {BORDER}; border-radius: 4px; background: {PANEL};
  text-align: center; height: 13px; color: {INK};
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 1px; }}
QScrollBar::handle:vertical {{ background: #cdc6b8; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: #cdc6b8; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- 其它 ---- */
QMessageBox {{ background: {PANEL}; }}
QMessageBox QLabel {{ color: {INK}; background: transparent; }}
QMessageBox QPushButton {{ min-width: 72px; }}
QDialog QLabel {{ color: {INK}; background: transparent; }}
QToolTip {{ background: {INK}; color: {PANEL}; border: none; padding: 4px 6px; font-size: 8pt; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QTextEdit, QPlainTextEdit {{
  background: {PANEL}; border: 1px solid {BORDER}; border-radius: 4px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; background: {PANEL}; }}
QTabBar::tab {{
  background: {ROW_ALT}; border: 1px solid {BORDER}; border-bottom: none;
  padding: 4px 12px; border-top-left-radius: 4px; border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{ background: {PANEL}; color: {ACCENT}; }}
QSplitter::handle {{ background: {BORDER}; width: 2px; }}
QToolButton {{
  background: transparent; border: 1px solid {BORDER}; border-radius: 4px;
  padding: 1px 6px; min-height: 16px;
}}
QToolButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QToolButton:checked {{ background: {ACCENT}; color: white; border-color: {ACCENT}; }}
QScrollArea {{ border: none; background: {BG}; }}
QScrollArea > QWidget > QWidget {{ background: {BG}; }}
QStackedWidget {{ background: {BG}; }}
QStackedWidget > QWidget {{ background: {BG}; }}
"""
