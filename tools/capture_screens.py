# -*- coding: utf-8 -*-
"""离屏渲染主窗口各页面并截图（用于交付验证）"""
import os, sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

OUT = _ROOT / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

app = QApplication.instance() or QApplication(sys.argv)
from app.gui.main_window import MainWindow

win = MainWindow()
win.resize(1400, 860)
win.show()
app.processEvents()

# 首页
win.nav.setCurrentRow(0); app.processEvents()
win.grab().save(str(OUT / "01_首页.png"))

# 高斯投影换带页：填一行 xy→BL
gz = win.pages[1]
gz.table_in.insertRow(0)
for j, val in enumerate(["G1", "3099348.64467", "352447.704379"]):
    gz.table_in.setItem(0, j, __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"]).QTableWidgetItem(val))
gz.rb_dst_bl.setChecked(True)
gz.run()
win.nav.setCurrentRow(1); app.processEvents()
win.grab().save(str(OUT / "02_高斯投影换带.png"))

# 空间坐标转换页：BLH→XYZ
sp = win.pages[2]
sp.table_in.insertRow(0)
for j, val in enumerate(["S1", "28.00000000", "103.18000000", "100"]):
    sp.table_in.setItem(0, j, __import__("PySide6.QtWidgets", fromlist=["QTableWidgetItem"]).QTableWidgetItem(val))
for rb in sp.type_rb.values():
    rb.setChecked(rb is sp.type_rb["BLH->XYZ"])
sp.run()
win.nav.setCurrentRow(2); app.processEvents()
win.grab().save(str(OUT / "03_空间坐标转换.png"))

# 参数应用页 / 控制点转换页 / 照片POS / CAD
win.nav.setCurrentRow(3); app.processEvents()
win.grab().save(str(OUT / "04_参数应用转换.png"))
win.nav.setCurrentRow(4); app.processEvents()
win.grab().save(str(OUT / "05_控制点转换.png"))
win.nav.setCurrentRow(5); app.processEvents()
win.grab().save(str(OUT / "06_照片POS处理.png"))
win.nav.setCurrentRow(6); app.processEvents()
win.grab().save(str(OUT / "07_CAD转换.png"))

print("screenshots saved to", OUT)
win.close()
