# -*- coding: utf-8 -*-
"""指示器图标资源生成 + QSS image 方案。

背景：样式表里只要有匹配 QCheckBox/QRadioButton 的规则（含通用 * / QWidget 规则），
QStyleSheetStyle 就接管指示器绘制，原生对勾消失；纯 QSS 又画不出对勾。
方案：启动时用 QPainter 生成 4 张指示器 PNG（复选/单选 × 选中/未选中），
QSS 以 image: url(绝对路径) 引用——复选框呈现"矩形框+对勾"、单选钮"圆形框+圆点"。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap, QPen

# 与 styles.py 同源的颜色常量（本地定义，避免循环导入）
ACCENT = "#2a6f8e"
BORDER = "#d8d2c4"
PANEL = "#ffffff"
MUTED = "#6b7a83"

_ASSET_DIR = None


def _writable_dir() -> Path:
    """优先程序内 assets/（便于打包），不可写则退到用户临时目录。"""
    here = Path(__file__).resolve().parent / "assets"
    try:
        here.mkdir(parents=True, exist_ok=True)
        probe = here / ".probe"
        probe.write_bytes(b"ok")
        probe.unlink()
        return here
    except OSError:
        d = Path(tempfile.gettempdir()) / "georefine_assets"
        d.mkdir(parents=True, exist_ok=True)
        return d


def asset_dir() -> Path:
    global _ASSET_DIR
    if _ASSET_DIR is None:
        _ASSET_DIR = _writable_dir()
    return _ASSET_DIR


def _draw_box(p: QPainter, size: int, on: bool, accent: str, edge: str) -> None:
    m = max(1.0, size * 0.08)
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.setPen(QPen(QColor(edge), max(1.0, size * 0.09)))
    p.setBrush(QColor(PANEL))
    p.drawRoundedRect(rect, 2, 2)
    if on:
        p.setPen(QPen(QColor(accent), max(1.6, size * 0.12),
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        k = size * 0.24
        cx, cy = size / 2, size / 2
        p.drawLine(QPointF(cx - k, cy + k * 0.15), QPointF(cx - k * 0.2, cy + k))
        p.drawLine(QPointF(cx - k * 0.2, cy + k), QPointF(cx + k * 0.9, cy - k * 0.7))


def _draw_circle(p: QPainter, size: int, on: bool, accent: str, edge: str) -> None:
    m = max(1.0, size * 0.08)
    rect = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.setPen(QPen(QColor(edge), max(1.0, size * 0.09)))
    p.setBrush(QColor(PANEL))
    p.drawEllipse(rect)
    if on:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(accent))
        cx = cy = size / 2
        rr = size * 0.22
        p.drawEllipse(QPointF(cx, cy), rr, rr)


def ensure_indicator_assets() -> dict:
    """生成（如缺失）4 张指示器 PNG，返回 {键: 绝对路径}。"""
    d = asset_dir()
    logical, scale = 14, 4        # 4x 超采样：56px 画布绘制后缩到 14px，抗锯齿干净
    canvas = logical * scale
    specs = {
        "chk_off":  lambda p: _draw_box(p, canvas, False, ACCENT, BORDER),
        "chk_on":   lambda p: _draw_box(p, canvas, True, ACCENT, ACCENT),
        "rad_off":  lambda p: _draw_circle(p, canvas, False, ACCENT, BORDER),
        "rad_on":   lambda p: _draw_circle(p, canvas, True, ACCENT, ACCENT),
    }
    out = {}
    for key, draw in specs.items():
        f = d / f"ind_{key}.png"
        pm = QPixmap(canvas, canvas)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        draw(p)
        p.end()
        small = pm.scaled(logical, logical, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        small.save(str(f))
        out[key] = str(f)
    return out
