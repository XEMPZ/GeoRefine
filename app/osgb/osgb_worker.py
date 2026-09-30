"""OSGB 模型转换的后台工作线程（UI 线程永不做重活）。"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from app.osgb import flash


class OsgbConvertWorker(QThread):
    """整模型 OSGB 坐标/高程转换。

    进度语义：C++ 工具一次调用处理整批，中途无法插入进度回调，
    因此这里给出"阶段 + 心跳"式进度，而不是逐瓦片精确百分比。
    """

    tick = Signal(str)                      # 阶段描述
    finished_ok = Signal(object)            # flash.FlashResult
    failed = Signal(str)

    def __init__(self, model_dir, out_dir, param_doc, *, mode="xyz", binary="",
                 options=flash.DEFAULT_OPTIONS, threads=16, keep_origin=True,
                 proj_override=None, parent=None):
        super().__init__(parent)
        self.model_dir = str(model_dir)
        self.out_dir = str(out_dir)
        self.param_doc = param_doc
        self.mode = mode
        self.binary = binary
        self.options = options
        self.threads = int(threads)
        self.keep_origin = bool(keep_origin)
        self.proj_override = proj_override      # 界面源侧投影覆盖（带号/L0/假东）
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            self.tick.emit("正在扫描模型…")
            res = flash.convert_model_fast(
                self.model_dir, self.out_dir, self.param_doc,
                mode=self.mode, binary=self.binary, options=self.options,
                threads=self.threads, keep_origin=self.keep_origin,
                proj_override=self.proj_override,
                cancel=self._cancel.is_set)
            if self._cancel.is_set():
                self.failed.emit("已取消")
                return
            self.finished_ok.emit(res)
        except Exception as exc:  # noqa: BLE001 —— 错误回传 UI，不静默
            self.failed.emit(f"{type(exc).__name__}: {exc}")
