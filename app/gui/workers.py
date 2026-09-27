"""后台工作线程：照片扫描/批处理、CAD 批处理。UI 线程永不做重活。"""
from __future__ import annotations

import os
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from app.photo import batch as photo_batch


class ScanWorker(QThread):
    done = Signal(int, str)          # 数量, 错误
    progress = Signal(int, int, str)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self.roots = [roots] if isinstance(roots, str) else list(roots)

    def run(self):
        try:
            total = 0
            err = ""
            for root in self.roots:
                try:
                    total += len(photo_batch.scan_photos(root))
                except Exception as e:  # noqa: BLE001
                    err = f"{root}: {e}"
            self.done.emit(total, err)
        except Exception as e:  # noqa: BLE001
            self.done.emit(0, str(e))


class PhotoBatchWorker(QThread):
    progress = Signal(int, int, str)          # done, total, current
    finished_ok = Signal(dict)                # result dict
    failed = Signal(str)

    def __init__(self, root, spec: dict, update_xmp=True, parent=None,
                 plane_fn=None, plane_name: str = "", dry_run: bool = False):
        super().__init__(parent)
        self.roots = [root] if isinstance(root, str) else list(root)   # 支持多目录
        self.spec = spec                 # 高程转换方式描述，在线程内构造转换器（大格网解码不冻结 UI）
        self.update_xmp = update_xmp
        self.plane_fn = plane_fn         # 可选平面坐标转换 (lon, lat) -> (x, y)
        self.plane_name = plane_name
        self.dry_run = dry_run           # True = 仅计算预览（不备份不回写）
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            from app.common import hconv
            spec = self.spec
            mode = spec.get("mode", "offset")
            if mode == "geoid":
                from app.core.geoid import GridModel, load_grid
                model = GridModel(load_grid(spec["path"]))   # 大格网首次解码在工作线程
                fn = hconv.make_photo_height_fn("geoid", model=model)
                mode_name = f"格网:{model.grid.name}"
            elif mode == "param":
                fn = hconv.make_photo_height_fn("param", param=spec.get("param"))
                mode_name = f"参数:{(spec.get('param') or {}).get('name')}"
            else:
                off = float(spec.get("offset", 0.0))
                fn = hconv.make_photo_height_fn("offset", offset=off)
                mode_name = f"偏移{off:+.3f}m"
            merged = {"rows": [], "ok": 0, "failed": 0, "skipped": 0, "total": 0,
                      "ledger_csv": None, "roots": self.roots}
            done = 0
            for root in self.roots:
                if self.cancel_event.is_set():
                    break
                res = photo_batch.process(root, fn, mode_name=mode_name,
                                          cancel=self.cancel_event,
                                          progress_cb=lambda d, t, c, done=done: self.progress.emit(
                                              done + d, t, c),
                                          update_xmp=self.update_xmp,
                                          plane_fn=self.plane_fn, plane_name=self.plane_name,
                                          dry_run=self.dry_run)
                merged["rows"] += res["rows"]
                for k in ("ok", "failed", "skipped", "total"):
                    merged[k] += res[k]
                merged["ledger_csv"] = res["ledger_csv"] if merged["ledger_csv"] is None \
                    else "；".join([merged["ledger_csv"], res["ledger_csv"]])
                merged["dry_run"] = res.get("dry_run", False)
                done += res["total"]
            res = merged
            self.finished_ok.emit(res)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class RollbackWorker(QThread):
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self.roots = [roots] if isinstance(roots, str) else list(roots)

    def run(self):
        try:
            merged = {"restored": 0, "missing_backup": 0, "total": 0}
            for root in self.roots:
                r = photo_batch.rollback(root)
                for k in ("restored", "missing_backup", "total"):
                    merged[k] += r[k]
            merged["roots"] = self.roots
            self.finished_ok.emit(merged)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class CadWorker(QThread):
    progress = Signal(int, int, str)
    one_done = Signal(dict)
    finished_ok = Signal(int)      # 成功文件数
    failed = Signal(str)

    def __init__(self, jobs: list[dict], parent=None):
        super().__init__(parent)
        self.jobs = jobs          # [{in, out, planar, height_fn, apply_2d}]
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        from app.cad.dxf_transform import transform_dxf
        ok = 0
        try:
            for ji, job in enumerate(self.jobs):
                if self.cancel_event.is_set():
                    break
                try:
                    kind = job.get("kind", "dxf")
                    if kind == "dxf":
                        res = transform_dxf(job["in"], job["out"], planar=job.get("planar"),
                                            height_fn=job.get("height_fn"),
                                            apply_2d_elevation=job.get("apply_2d", False),
                                            cancel=self.cancel_event)
                    elif kind == "shp":
                        from app.fileconv.shp_transform import transform_shp
                        res = transform_shp(job["in"], job["out"],
                                            planar_fn=job.get("planar_fn"),
                                            height_fn=job.get("height_fn"),
                                            cancel=self.cancel_event)
                    elif kind == "dwg":
                        from app.fileconv.dwg_convert import DwgConverter
                        from app.cad.dxf_transform import transform_dxf as _tx
                        tmp_dxf = str(Path(job["out"]).with_suffix(".原始.dxf"))
                        with DwgConverter() as _dc:
                            _dc.convert(job["in"], tmp_dxf)
                        res = _tx(tmp_dxf, job["out"], planar=job.get("planar"),
                                  height_fn=job.get("height_fn"),
                                  apply_2d_elevation=job.get("apply_2d", False),
                                  cancel=self.cancel_event)
                        try:
                            os.remove(tmp_dxf)
                        except OSError:
                            pass
                    elif kind == "mdb":
                        from app.fileconv.pgdb_transform import transform_pgdb
                        res = transform_pgdb(job["in"], job["out"],
                                             planar_fn=job.get("planar_fn"),
                                             height_fn=job.get("height_fn"),
                                             cancel=self.cancel_event)
                    else:
                        from app.fileconv.points_text import transform_points_text
                        res = transform_points_text(job["in"], job["out"],
                                                    planar_fn=job.get("planar_fn"),
                                                    height_fn=job.get("height_fn"),
                                                    input_lonlat=job.get("input_lonlat"),
                                                    cancel=self.cancel_event)
                    self.one_done.emit({"file": job["in"], "result": res})
                    ok += 1
                except Exception as e:  # noqa: BLE001
                    self.one_done.emit({"file": job["in"], "error": str(e)})
                self.progress.emit(ji + 1, len(self.jobs), job["in"])
            self.finished_ok.emit(ok)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))
