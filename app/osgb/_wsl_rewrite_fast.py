"""顶点改写（快速版）——供 WSL 内 python3 调用，纯标准库。

优化要点（对照 v1 的 0.441 s / 20.7 MB ≈ 37 MB/s）
--------------------------------------------------
1. **不做逐行正则**：只对少量"结构行"用 startswith 判定，顶点行用 str.split() 取字段。
   正则在 8000 行 × 多次匹配上是主要热点。
2. **整块批量处理**：定位到 osg::Vec3Array 的 vector 块后一次性切片处理该块的所有行，
   避免逐行调用函数。
3. **系数局部化**：把 A、b、origin、new_origin 从列表提升成局部标量，
   消除内层循环里的多重下标。
4. **单次 join 输出**：用列表收集 + "".join()，避免多次 write。

改写语义与 v1 完全一致：out = A @ (p + origin) + b − new_origin。
"""
import json
import sys


def _is_anchor(line):
    """顶点数组对象头 osg::Vec3Array（注意有缩进）。"""
    s = line.lstrip()
    return s.startswith("osg::Vec3Array") and s.rstrip().endswith("{")


def _vec_header(line):
    """vector N → N，或 None（允许缩进）。"""
    s = line.lstrip()
    if not s.startswith("vector "):
        return None
    s = s.rstrip()
    if not s.endswith("{"):
        return None
    head = s[:-1].strip()
    if not head.startswith("vector "):
        return None
    num = head[7:].strip()
    if not num.isdigit():
        return None
    return int(num)


def main(src, dst, params_path):
    cfg = json.load(open(params_path, encoding="utf-8"))
    o0, o1, o2 = cfg["origin"]
    n0, n1, n2 = cfg["new_origin"]
    a = cfg["A"]
    a00, a01, a02 = a[0]
    a10, a11, a12 = a[1]
    a20, a21, a22 = a[2]
    b0, b1, b2 = cfg["b"]

    with open(src, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    out = []
    i = 0
    n = len(lines)
    nb = 0
    nv = 0
    while i < n:
        line = lines[i]
        if not _is_anchor(line):
            out.append(line)
            i += 1
            continue
        # 锚点之后 200 行内找 vector 块
        j = i + 1
        limit = min(n, i + 200)
        cnt = None
        while j < limit:
            c = _vec_header(lines[j])
            if c is not None:
                cnt = c
                break
            j += 1
        if cnt is None or cnt <= 0 or j + 1 + cnt > n:
            out.append(line)
            i += 1
            continue
        # 校验紧随 cnt 行确实是三浮点
        ok = True
        for k in range(j + 1, j + 1 + cnt):
            if lines[k].count(" ") < 2:
                ok = False
                break
        if not ok:
            out.append(line)
            i += 1
            continue

        out.extend(lines[i:j + 1])
        nl = "\n" if lines[j].endswith("\n") else ""
        indent = lines[j][:len(lines[j]) - len(lines[j].lstrip())]
        pad = indent + "  "
        for k in range(j + 1, j + 1 + cnt):
            parts = lines[k].split()
            if len(parts) < 3:
                out.append(lines[k])
                continue
            x = float(parts[0]) + o0
            y = float(parts[1]) + o1
            z = float(parts[2]) + o2
            u = a00 * x + a01 * y + a02 * z + b0 - n0
            v = a10 * x + a11 * y + a12 * z + b1 - n1
            w = a20 * x + a21 * y + a22 * z + b2 - n2
            out.append(pad)
            out.append("%.7f" % u)
            out.append(" ")
            out.append("%.7f" % v)
            out.append(" ")
            out.append("%.7f" % w)
            out.append(nl)
            nv += 1
        nb += 1
        i = j + 1 + cnt

    with open(dst, "w", encoding="utf-8") as f:
        f.write("".join(out))
    sys.stdout.write("%d %d" % (nb, nv))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
