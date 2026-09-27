"""无人机照片 POS 读取与回写（EXIF GPS 为主，DJI XMP 兼容）。

- EXIF GPS: GPSLatitude/Ref、GPSLongitude/Ref、GPSAltitude（大地高，米）
- XMP drone-dji: AbsoluteAltitude / RelativeAltitude / GpsLatitude / GpsLongitude（best-effort）
- 回写仅动 GPSAltitude（rational 分母 100，毫米精度），其余 EXIF 字段一字不动；
  XMP AbsoluteAltitude 采用等字节替换（best-effort，失败不崩）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import piexif
from PIL import Image


@dataclass
class PosRecord:
    lat: float | None = None
    lon: float | None = None
    h_ell: float | None = None
    source: str = "none"          # exif | xmp | none
    abs_alt: float | None = None
    rel_alt: float | None = None
    raw: dict = field(default_factory=dict)


def _rational_to_float(r):
    try:
        num, den = r[0], r[1]
        return float(num) / float(den) if den else 0.0
    except Exception:
        return 0.0


def _dms_to_deg(dms, ref: str) -> float:
    deg = _rational_to_float(dms[0]) + _rational_to_float(dms[1]) / 60.0 + _rational_to_float(dms[2]) / 3600.0
    if ref and ref.strip() in ("S", "W"):
        deg = -deg
    return deg


def extract_pos(jpg_path) -> PosRecord:
    """提取照片 POS。EXIF GPS 优先，XMP 兜底；两者都无则 source='none'。"""
    path = str(jpg_path)
    rec = PosRecord()
    try:
        img = Image.open(path)
        xmp_bytes = img.info.get("xmp", b"") or b""
        img.close()
    except Exception:
        xmp_bytes = b""
    # XMP drone-dji 字段（best-effort）
    try:
        txt = xmp_bytes.decode("utf-8", "ignore") if isinstance(xmp_bytes, (bytes, bytearray)) else str(xmp_bytes)

        def _xmp(attr):
            m = re.search(rf'drone-dji:{attr}="([^"]+)"', txt)
            return m.group(1) if m else None

        if txt:
            rec.raw["xmp"] = {a: _xmp(a) for a in
                              ("AbsoluteAltitude", "RelativeAltitude", "GpsLatitude", "GpsLongitude")}
            for k in ("AbsoluteAltitude", "RelativeAltitude"):
                v = rec.raw["xmp"].get(k)
                if v is not None:
                    try:
                        setattr(rec, {"AbsoluteAltitude": "abs_alt", "RelativeAltitude": "rel_alt"}[k], float(v))
                    except ValueError:
                        pass
    except Exception:
        pass
    # EXIF GPS（优先）
    try:
        exif = piexif.load(path)
        gps = exif.get("GPS", {}) or {}
        if gps.get(piexif.GPSIFD.GPSLatitude) and gps.get(piexif.GPSIFD.GPSLongitude):
            rec.lat = _dms_to_deg(gps[piexif.GPSIFD.GPSLatitude],
                                  gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N").decode() if isinstance(
                                      gps.get(piexif.GPSIFD.GPSLatitudeRef), bytes) else "N")
            rec.lon = _dms_to_deg(gps[piexif.GPSIFD.GPSLongitude],
                                  gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E").decode() if isinstance(
                                      gps.get(piexif.GPSIFD.GPSLongitudeRef), bytes) else "E")
        alt = gps.get(piexif.GPSIFD.GPSAltitude)
        if alt:
            rec.h_ell = _rational_to_float(alt)
            # GPSAltitudeRef: 0=海平面以上, 1=以下（EXIF 规范；piexif 可能存 int 或 bytes）
            ref = gps.get(piexif.GPSIFD.GPSAltitudeRef, 0)
            if isinstance(ref, (bytes, bytearray)):
                ref = int(ref[0]) if ref else 0
            if ref == 1:
                rec.h_ell = -abs(rec.h_ell)
        if rec.lat is not None and rec.h_ell is not None:
            rec.source = "exif"
        elif rec.lat is not None:
            rec.source = "exif"
        elif rec.raw.get("xmp") and rec.raw["xmp"].get("GpsLatitude"):
            try:
                rec.lat = float(rec.raw["xmp"]["GpsLatitude"])
                rec.lon = float(rec.raw["xmp"].get("GpsLongitude") or 0.0)
                rec.source = "xmp"
            except (TypeError, ValueError):
                pass
    except Exception:
        pass
    return rec


def set_gps_altitude(jpg_path, new_h: float) -> bool:
    """重写 EXIF GPSAltitude（rational，分母 100）。其余字段不动。"""
    path = str(jpg_path)
    try:
        exif = piexif.load(path)
        gps = exif.get("GPS", {}) or {}
        # EXIF rational 无符号：负高程必须 |值|+Ref=1，否则 piexif.dump 抛错且读侧丢符号
        gps[piexif.GPSIFD.GPSAltitude] = (int(round(abs(new_h) * 100)), 100)
        gps[piexif.GPSIFD.GPSAltitudeRef] = 0 if new_h >= 0 else 1
        exif["GPS"] = gps
        # "1st"（缩略图 IFD）原样保留，避免丢嵌入预览图；dump 会原样复制缩略图字节
        out = piexif.insert(piexif.dump(exif), path)
        if out is not None:
            # 某些版本返回拼接后的完整 JPEG 字节（不写盘），需手动落盘
            with open(path, "wb") as f:
                f.write(out)
        return True
    except Exception:
        return False


def set_xmp_absolute_altitude(jpg_path, new_h: float) -> bool:
    """等字节替换 APP1 XMP 段内 drone-dji:AbsoluteAltitude 值（best-effort）。"""
    path = Path(jpg_path)
    try:
        data = bytearray(path.read_bytes())
        marker = b'http://ns.adobe.com/xap/1.0/\x00'
        i = data.find(marker)
        if i < 0:
            return False
        seg_len = int.from_bytes(data[i - 2:i], "big")
        seg_end = i - 2 + 2 + seg_len
        seg = bytes(data[i + len(marker):seg_end])
        m = re.search(rb'drone-dji:AbsoluteAltitude="([^"]*)"', seg)
        if not m:
            return False
        old, new = m.group(1), f"{new_h:+.2f}".encode()
        if len(new) > len(old):
            new = new[:len(old)]
        new_seg = seg[:m.start(1)] + new + b" " * (len(old) - len(new)) + seg[m.end(1):]
        if len(new_seg) != len(seg):
            return False
        data[i + len(marker):seg_end] = new_seg
        path.write_bytes(bytes(data))
        return True
    except Exception:
        return False
