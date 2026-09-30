"""GeoRefine — 似大地水准面精化与坐标转换软件"""

__version__ = "1.1.0"
APP_TITLE = "似大地水准面精化与坐标转换软件 GeoRefine v1.1"


def app_root():
    """返回「软件目录」——存放 params/ models/ logs/ config.json 的可写目录。

    * 源码运行：项目根目录（app/ 的上一级）。
    * PyInstaller 打包后：**可执行文件所在目录**。打包版把资源放在 exe 旁边，
      而不是临时解包目录（_MEIPASS），这样参数库、格网模型、台账在重启后仍在，
      用户也能直接编辑 config.json。
    """
    import sys
    from pathlib import Path
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent
