"""点云（LAS）转换的后台工作线程。"""
from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal

from app.pointcloud import las_job


class LasConvertWorker(QThread):
    """整目录 LAS 转换。逐文件汇报进度。"""

    file_progress = Signal(int, int, str)      # idx, total, name
    point_progress = Signal(int, int)          # done, total（当前文件）
    finished_ok = Signal(object)               # las_job.LasJobResult
    failed = Signal(str)

    def __init__(self, src_root, dst_root, param_doc, *, mode="xyz",
                 proj_override=None, chunk_mb=64.0, parent=None):
        super().__init__(parent)
        self.src_root = str(src_root)
        self.dst_root = str(dst_root)
        self.param_doc = param_doc
        self.mode = mode
        self.proj_override = proj_override
        self.chunk_mb = float(chunk_mb)
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            res = las_job.convert_pointcloud(
                self.src_root, self.dst_root, self.param_doc,
                mode=self.mode, proj_override=self.proj_override,
                chunk_mb=self.chunk_mb,
                progress=lambda i, n, name: self.file_progress.emit(i, n, name),
                cancel=self._cancel.is_set)
            if self._cancel.is_set():
                self.failed.emit("已取消")
                return
            self.finished_ok.emit(res)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
