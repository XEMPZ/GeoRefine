"""角度格式回归：d.ms 编解码、格式化进位、d.ms/十进制度识别。"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.core import anglefmt  # noqa: E402


def run() -> int:
    checks = 0

    # 1) d.ms 解码
    assert abs(anglefmt.dms_to_deg(30.26345678) - (30 + 26 / 60 + 34.5678 / 3600)) < 1e-12
    assert abs(anglefmt.dms_to_deg(105.302568) - (105 + 30 / 60 + 25.68 / 3600)) < 1e-12
    assert abs(anglefmt.dms_to_deg(-45.3) + (45 + 30 / 60)) < 1e-12
    checks += 1

    # 2) 满 59′59″ 的格式化不能出现 60′ 或负秒
    s = anglefmt.fmt_dms(math.radians(30 + 59 / 60 + 59 / 3600))
    assert s == "30°59′59.0000″", s
    checks += 1
    # 秒位四舍五入到整分要进位
    s = anglefmt.fmt_dms(math.radians(45 + 59 / 60 + 59.999999 / 3600))
    assert s == "46°00′00.0000″", s
    checks += 1
    # 普通值
    assert anglefmt.fmt_dms(math.radians(30 + 26 / 60 + 34.5678 / 3600)) == "30°26′34.5678″"
    checks += 1

    # 3) 度 ↔ 弧度 ↔ d.ms 往返
    worst = 0.0
    for deg in (-179.5, -45.0, -0.25, 0.0, 0.25, 30.4429355, 105.507133333, 179.5):
        r = math.radians(deg)
        back = anglefmt.dms_to_rad(anglefmt.rad_to_dms(r))
        worst = max(worst, abs(back - r) * 648000.0 / math.pi)     # 角秒
    assert worst < 1e-3, worst
    checks += 1

    # 4) 输入解析：空格分隔 / d.ms / 十进制度
    assert abs(anglefmt.parse_flexible("30 26 34.5678", "dms")
               - math.radians(30 + 26 / 60 + 34.5678 / 3600)) < 1e-12
    assert abs(anglefmt.parse_flexible("30.26345678", "dms")
               - math.radians(30 + 26 / 60 + 34.5678 / 3600)) < 1e-12
    assert abs(anglefmt.parse_flexible("30.44293799", "deg") - math.radians(30.44293799)) < 1e-12
    assert abs(anglefmt.parse_flexible("-45 30", "dms") + math.radians(45.5)) < 1e-12
    checks += 1

    # 5) d.ms 结构判别（分/秒 <60 才可能是 d.ms）
    assert anglefmt.dms_plausible(30.26345678) is True
    assert anglefmt.dms_plausible(25.905919) is False      # 90′59″ → 必为十进制度
    assert anglefmt.dms_plausible(105.302568) is True
    checks += 1

    return checks
