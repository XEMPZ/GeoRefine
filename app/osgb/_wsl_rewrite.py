"""按线性系数改写 osgt 里的 Vec3Array 顶点（纯标准库，供 WSL 内 python3 调用）。

系数由 Windows 侧算好，本脚本只做算术；不含任何投影/拟合逻辑。
"""
import json
import re
import sys

VEC_ANCHOR = re.compile(r"^\s*osg::Vec3Array\s*\{")
VEC = re.compile(r"^(\s*)vector (\d+) \{\s*$")
F3 = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
                r"\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)"
                r"\s+(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*$")


def find_blocks(lines):
    """定位 osg::Vec3Array 内部的 vector 块（同 osgb_job._find_vertex_blocks）。"""
    out = []
    i = 0
    n = len(lines)
    while i < n:
        if not VEC_ANCHOR.match(lines[i]):
            i += 1
            continue
        j = i + 1
        limit = min(n, i + 200)
        while j < limit:
            m = VEC.match(lines[j])
            if m:
                indent = m.group(1)
                count = int(m.group(2))
                k = j + 1
                taken = 0
                while k < n and taken < count:
                    if not F3.match(lines[k]):
                        break
                    k += 1
                    taken += 1
                if taken == count and count > 0:
                    out.append((j, k, indent))
                    i = k
                else:
                    i = j + 1
                break
            j += 1
        else:
            i += 1
    return out


def main(src, dst, params_path):
    p = json.load(open(params_path, encoding="utf-8"))
    origin = p["origin"]
    new_origin = p["new_origin"]
    a = p["A"]              # 3x3
    b = p["b"]              # 3
    lines = open(src, encoding="utf-8", errors="replace").read().splitlines(keepends=True)
    plain = [l.rstrip("\n").rstrip("\r") for l in lines]
    blocks = find_blocks(plain)
    out = []
    cur = 0
    nb = 0
    nv = 0
    for (h, e, indent) in blocks:
        out.extend(lines[cur:h + 1])
        nl = "\n" if lines[h].endswith("\n") else ""
        for k in range(h + 1, e):
            mm = F3.match(plain[k])
            x = float(mm.group(1)) + origin[0]
            y = float(mm.group(2)) + origin[1]
            z = float(mm.group(3)) + origin[2]
            u = a[0][0] * x + a[0][1] * y + a[0][2] * z + b[0] - new_origin[0]
            v = a[1][0] * x + a[1][1] * y + a[1][2] * z + b[1] - new_origin[1]
            w = a[2][0] * x + a[2][1] * y + a[2][2] * z + b[2] - new_origin[2]
            out.append(indent + "  " + "%.10f %.10f %.10f" % (u, v, w) + nl)
            nv += 1
        cur = e
        nb += 1
    out.extend(lines[cur:])
    open(dst, "w", encoding="utf-8").write("".join(out))
    sys.stdout.write("%d %d" % (nb, nv))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
