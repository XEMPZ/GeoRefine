"""DWG → DXF 自动转换（AutoCAD COM 自动化，供文件转换页调用）。

icoord 的 DWG 链路是 ODA Teigha 32 位加壳模块，无法复用；本机装有 AutoCAD（2023 已验证），
用 COM 后台实例 SaveAs DXF（实测 AcSaveAsType=49 产出标准 DXF，ezdxf 可读，30s 全链路含启动）。
CASS 图的扩展属性（编码 XDATA）与块参照在 DXF 中原样保留。
"""
from __future__ import annotations

import os
from pathlib import Path

_DXFTYPE = 49          # AcSaveAsType：实测 49 产出 DXF（61/60/55 报错，49 为 2013 DXF 档）
_RPC_E_CALL_REJECTED = -2147418111   # 0x80010001：AutoCAD/CASS 忙时拒绝 COM 呼叫


def _com_retry(fn, total_seconds: float = 60.0, interval: float = 1.0):
    """RPC_E_CALL_REJECTED 自动重试（目标程序忙是常态，如 CASS 正在执行命令）。"""
    import time

    import pywintypes

    deadline = time.time() + total_seconds
    while True:
        try:
            return fn()
        except pywintypes.com_error as e:
            if e.hresult == _RPC_E_CALL_REJECTED and time.time() < deadline:
                time.sleep(interval)
                continue
            if e.hresult == _RPC_E_CALL_REJECTED:
                raise RuntimeError("AutoCAD 忙（可能正在执行命令或有模态对话框），已等待 60s 仍被拒绝") from e
            raise


def find_acad() -> str | None:
    """探测本机 AutoCAD acad.exe（C:/D 盘常见安装位）。"""
    cands = []
    for drive in ("C", "D", "E"):
        root = Path(f"{drive}:/Program Files/Autodesk")
        if root.exists():
            cands += sorted(root.glob("AutoCAD 20*/acad.exe"), reverse=True)
            root2 = Path(f"{drive}:/Program Files (x86)/Autodesk")
            if root2.exists():
                cands += sorted(root2.glob("AutoCAD 20*/acad.exe"), reverse=True)
    return str(cands[0]) if cands else None


class DwgConverter:
    """AutoCAD COM 实例；with 语法管理生命周期（一个实例可连续转多个文件）。

    实测（AutoCAD 2023）：DispatchEx 起的独立进程 SaveAs 必败，Dispatch
    （附着运行中的实例或启动默认实例）才可用。附着用户已开实例时不动其
    Visible、绝不 Quit，避免干扰用户会话。
    """

    def __init__(self):
        self._acad = None
        self._owned = False

    def __enter__(self):
        self.open_app()
        return self

    def __exit__(self, *a):
        self.quit()

    def open_app(self):
        if self._acad is not None:
            return self._acad
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            acad = win32com.client.GetActiveObject("AutoCAD.Application")
            _com_retry(lambda: acad.Version, total_seconds=5.0)        # 健康检查（短重试）
            _com_retry(lambda: acad.Documents, total_seconds=5.0)
            self._acad = acad
            self._owned = False     # 用户自己的会话：不 Visible、不 Quit
        except Exception:  # noqa: BLE001
            acad = win32com.client.Dispatch("AutoCAD.Application")
            self._owned = True
            try:
                acad.Visible = False
                acad.DisplayAlerts = False
            except Exception:  # noqa: BLE001
                pass
            self._acad = acad
        return self._acad

    def convert(self, dwg_path, out_dxf) -> str:
        """DWG → DXF。返回 out_dxf 路径；失败抛异常（COM 引用失效自动换新实例重试一次）。"""
        for attempt in (0, 1):
            try:
                return self._convert_once(dwg_path, out_dxf)
            except Exception:  # noqa: BLE001
                self.quit()         # 丢弃可能失效的引用；owned 时顺带 Quit 僵尸实例
                if attempt == 1:
                    raise

    def _convert_once(self, dwg_path, out_dxf) -> str:
        dwg_path, out_dxf = str(dwg_path), str(out_dxf)
        if not Path(dwg_path).exists():
            raise FileNotFoundError(dwg_path)
        acad = self.open_app()
        doc = _com_retry(lambda: acad.Documents.Open(dwg_path, True))   # readonly
        try:
            _com_retry(lambda: doc.SaveAs(out_dxf, _DXFTYPE))
        except Exception as e:  # noqa: BLE001
            try:
                doc.Close(False)
            except Exception:  # noqa: BLE001
                pass
            raise RuntimeError(f"AutoCAD SaveAs 失败: {e}") from e
        try:
            _com_retry(lambda: doc.Close(False))
        except Exception:  # noqa: BLE001
            pass
        if not Path(out_dxf).exists():
            raise RuntimeError("AutoCAD 未产出 DXF 文件")
        return out_dxf

    def quit(self):
        if self._acad is not None and self._owned:
            try:
                self._acad.Quit()
            except Exception:  # noqa: BLE001
                pass
        self._acad = None
        try:
            import pythoncom
            pythoncom.CoUninitialize()
        except Exception:  # noqa: BLE001
            pass


def dwg_to_dxf(dwg_path, out_dxf) -> str:
    """单文件便捷入口（每次起停一个后台 AutoCAD 实例；批量请用 DwgConverter）。"""
    with DwgConverter() as dc:
        return dc.convert(dwg_path, out_dxf)
