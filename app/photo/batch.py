"""照片 POS 批量处理：递归扫描、高程转换、备份、台账、回滚。

线程模型：本模块不启动线程（线程由 GUI 层管理），无共享可变状态，可安全被工作线程调用。
"""
from __future__ import annotations

import shutil
import threading
from datetime import datetime
from pathlib import Path

from app.common.io_csv import write_csv_utf8bom
from app.photo.pos_io import extract_pos, set_gps_altitude, set_xmp_absolute_altitude

LEDGER_HEADER = ["序号", "文件路径", "相对路径", "纬度", "经度", "处理前椭球高m", "转换值v m",
                 "处理后高程m", "转换方式", "状态", "备份路径", "错误信息", "处理时间", "XMP"]
LEDGER_HEADER_PLANE = LEDGER_HEADER[:9] + ["转换后x(m)", "转换后y(m)"] + LEDGER_HEADER[9:]


def scan_photos(root: str, backup_dir_name: str = "_POS备份") -> list[Path]:
    """递归扫描目录及全部子目录下的 JPG（大小写不敏感），排除备份目录，排序稳定。"""
    root = Path(root)
    if not root.exists():
        raise FileNotFoundError(f"目录不存在: {root}")
    files = [p for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg")
             and backup_dir_name not in p.relative_to(root).parts]
    return sorted(files, key=lambda p: str(p).lower())


def _backup_path(root: Path, photo: Path, backup_dir_name: str) -> Path:
    rel = photo.relative_to(root)
    return root / backup_dir_name / rel


def process(root: str, height_fn, backup_dir_name: str = "_POS备份",
            cancel: threading.Event | None = None, progress_cb=None,
            update_xmp: bool = True, mode_name: str = "",
            plane_fn=None, plane_name: str = "", dry_run: bool = False) -> dict:
    """批量处理。

    height_fn(lon, lat, h_ell) -> (h_new, v, detail)；h_new 为 None 表示不可转换。
    plane_fn(lon, lat) -> (x, y) | None：可选的平面坐标转换（照片 B/L → 地方平面），
      提供时台账追加 转换后x/转换后y 两列。
    dry_run=True：仅计算预览（不备份、不回写、不写台账），状态记为"预览"。
    返回 {"rows", "ok", "failed", "skipped", "total", "ledger_csv", "root"}。
    台账行字段见 LEDGER_HEADER。status: ok/no_pos/out_of_range/write_failed/skipped。
    """
    root = Path(root)
    photos = scan_photos(str(root), backup_dir_name=backup_dir_name)
    total = len(photos)
    rows: list[dict] = []
    ok = failed = skipped = 0
    now = lambda: datetime.now().isoformat(timespec="seconds")  # noqa: E731
    for seq, photo in enumerate(photos, 1):
        if cancel is not None and cancel.is_set():
            skipped += 1
            rows.append({"seq": seq, "path": str(photo), "relpath": str(photo.relative_to(root)),
                         "status": "skipped", "error": "用户取消", "time": now()})
            continue
        row = {"seq": seq, "path": str(photo), "relpath": str(photo.relative_to(root)),
               "lat": None, "lon": None, "h_before": None, "v": None, "h_after": None,
               "px": None, "py": None, "xmp_ok": None,
               "mode": mode_name, "status": "", "backup": "", "error": "", "time": now()}
        try:
            pos = extract_pos(photo)
            row["lat"], row["lon"] = pos.lat, pos.lon
            row["h_before"] = pos.h_ell
            xmp_abs = (pos.raw.get("xmp") or {}).get("AbsoluteAltitude")
            row["xmp_ok"] = xmp_abs is not None
            if pos.h_ell is None or pos.lat is None or pos.lon is None:
                row["status"] = "no_pos"
                row["error"] = "未找到 POS 信息"
                failed += 1
                rows.append(row)
                if progress_cb:
                    progress_cb(seq, total, str(photo))
                continue
            h_new, v, detail = height_fn(pos.lon, pos.lat, pos.h_ell)
            row["v"] = v
            row["mode"] = detail or mode_name
            if h_new is None:
                row["status"] = "out_of_range"
                row["error"] = detail or "超出模型范围"
                failed += 1
                rows.append(row)
                if progress_cb:
                    progress_cb(seq, total, str(photo))
                continue
            if dry_run:
                row["status"] = "预览"
                row["h_after"] = h_new
                if plane_fn is not None:
                    try:
                        row["px"], row["py"] = plane_fn(pos.lon, pos.lat)
                        row["mode"] = (row["mode"] or mode_name) + f" + 平面({plane_name})"
                    except Exception as pe:  # noqa: BLE001
                        row["error"] = f"平面转换失败: {pe}"
                ok += 1
                rows.append(row)
                if progress_cb:
                    progress_cb(seq, total, str(photo))
                continue
            # 备份（仅首次：_POS备份 保存原始 POS/XMP，之后不再改动）
            bak = _backup_path(root, photo, backup_dir_name)
            if not bak.exists():
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(photo, bak)
            row["backup"] = str(bak)
            # 回写
            ok_write = set_gps_altitude(photo, h_new)
            if ok_write and update_xmp and row.get("xmp_ok"):
                set_xmp_absolute_altitude(photo, h_new)  # best-effort（仅可识别 XMP 的照片）
            if ok_write:
                row["status"] = "ok"
                row["h_after"] = h_new
                if plane_fn is not None:
                    try:
                        row["px"], row["py"] = plane_fn(pos.lon, pos.lat)
                        row["mode"] = (row["mode"] or mode_name) + f" + 平面({plane_name})"
                    except Exception as pe:  # noqa: BLE001
                        row["error"] = f"平面转换失败: {pe}"
                ok += 1
            else:
                row["status"] = "write_failed"
                row["error"] = "EXIF 写入失败"
                failed += 1
        except Exception as e:  # noqa: BLE001
            row["status"] = "write_failed"
            row["error"] = str(e)
            failed += 1
        rows.append(row)
        if progress_cb:
            progress_cb(seq, total, str(photo))
    if dry_run:
        return {"rows": rows, "ok": ok, "failed": failed, "skipped": skipped,
                "total": total, "ledger_csv": None, "root": str(root), "dry_run": True}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ledger = str(root / f"POS处理台账_{ts}.csv")
    header = LEDGER_HEADER_PLANE if plane_fn is not None else LEDGER_HEADER
    write_csv_utf8bom(ledger, header, [_row_to_list(r, plane_fn is not None) for r in rows])
    return {"rows": rows, "ok": ok, "failed": failed, "skipped": skipped,
            "total": total, "ledger_csv": ledger, "root": str(root)}


def _row_to_list(r: dict, with_plane: bool = False) -> list:
    base = [r.get("seq"), r.get("path"), r.get("relpath"), r.get("lat"), r.get("lon"),
            r.get("h_before"), r.get("v"), r.get("h_after"), r.get("mode")]
    if with_plane:
        base += [r.get("px"), r.get("py")]
    base += [r.get("status"), r.get("backup"), r.get("error"), r.get("time"),
             "有" if r.get("xmp_ok") else ("无" if r.get("xmp_ok") is not None else "")]
    return base


def rollback(root: str, backup_dir_name: str = "_POS备份") -> dict:
    """从 _POS备份 镜像结构还原照片，返回统计。"""
    root = Path(root)
    bak_root = root / backup_dir_name
    if not bak_root.exists():
        return {"restored": 0, "missing_backup": 0, "total": 0, "root": str(root)}
    restored = missing = 0
    for bak in sorted(bak_root.rglob("*")):
        if not bak.is_file() or bak.suffix.lower() not in (".jpg", ".jpeg"):
            continue
        rel = bak.relative_to(bak_root)
        target = root / rel
        if target.exists():
            shutil.copy2(bak, target)
            restored += 1
        else:
            missing += 1
    return {"restored": restored, "missing_backup": missing, "total": restored + missing,
            "root": str(root)}
