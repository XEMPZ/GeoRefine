"""在 WSL 内部批量执行 OSGB 转换（纯标准库，供 wsl python3 调用）。

性能要点（实测，2026-09-30，2.5 MB 瓦片，高档 NVMe）
----------------------------------------------------
| 做法 | 耗时 |
|---|---|
| osgconv 读 /mnt/e → 写 /tmp        | 0.42 s |
| osgconv 读 /mnt/e → 写 /mnt/e      | **53.4 s** |
| osgconv 读本地  → 写 /mnt/e        | **53.4 s** |
| osgconv 写本地 → 再 cp 到 /mnt/e   | 0.42 + 0.09 s |
| dd 预分配后再写 /mnt/e             | 53.4 s |
| cp 写 64 MB 到 /mnt/e              | 0.28 s |

结论：**慢的是 osgconv 向 /mnt 挂载点写**（127 倍），与读方向、文件分配都无关；
底层是它对挂载点做了不适合 9P 的写模式。因此铁律是：

    **osgconv 的输入输出一律在 WSL 本地盘；只有最终产物用 cp 搬回 /mnt。**

用法:
    python3 _wsl_batch.py jobs.json

jobs.json:
    {
      "osgconv": "/usr/bin/osgconv",
      "python": "python3",
      "rewriter": "/mnt/c/.../_wsl_rewrite.py",
      "params_path": "/mnt/c/.../params.json",
      "workers": 8,
      "jobs": [ ["/mnt/e/in/tile.osgb", "/mnt/e/out/tile.osgb"], ... ]
    }
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor


def convert(osgconv, src, dst, errlog):
    """osgconv 调用（src/dst 必须是 WSL 本地路径）。"""
    with open(errlog, "w") as err:
        p = subprocess.run([osgconv, "--compressed", src, dst], stdout=subprocess.DEVNULL, stderr=err,
                           timeout=1800)
    return p.returncode == 0 and os.path.exists(dst)


def main(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    osgconv = cfg.get("osgconv", "/usr/bin/osgconv")
    py = cfg.get("python", "python3")
    rewriter = cfg["rewriter"]
    params_path = cfg["params_path"]
    jobs = cfg["jobs"]
    workers = max(1, int(cfg.get("workers", 8)))

    root = tempfile.mkdtemp(prefix="osgbjob_")
    t0 = time.time()
    stats = {"ok": 0, "skip": 0, "fail": 0, "blocks": 0, "verts": 0, "bytes": 0}
    report = []
    lock_guard = __import__("threading").Lock()

    def one(idx, src, dst):
        w = os.path.join(root, "w%03d" % (idx % workers))
        os.makedirs(w, exist_ok=True)
        in_osgb = os.path.join(w, "in.osgb")
        mid = os.path.join(w, "mid.osgt")
        out_osgt = os.path.join(w, "out.osgt")
        out_osgb = os.path.join(w, "out.osgb")     # 本地暂存，最后才 cp
        errlog = os.path.join(w, "err.txt")
        try:
            if not os.path.exists(src):
                return (idx, "failed", "missing_src", 0, 0, 0)
            shutil.copyfile(src, in_osgb)                      # 顺序读挂载点（快）
            if not convert(osgconv, in_osgb, mid, errlog):
                return (idx, "failed", "read", 0, 0, 0)
            r = subprocess.run([py, rewriter, mid, out_osgt, params_path],
                               capture_output=True, text=True, timeout=1800)
            line = (r.stdout or "").strip().splitlines()
            if not line:
                return (idx, "failed", "rewrite", 0, 0, 0)
            nb_s, nv_s = line[-1].split()
            nb, nv = int(nb_s), int(nv_s)
            if nv == 0:
                return (idx, "skipped", "noverts", nb, nv, 0)
            if not convert(osgconv, out_osgt, out_osgb, errlog):   # 写本地（快）
                return (idx, "failed", "write", nb, nv, 0)
            d = os.path.dirname(dst)
            if d:
                os.makedirs(d, exist_ok=True)
            shutil.copyfile(out_osgb, dst)                          # 只此一处写挂载点
            return (idx, "ok", "", nb, nv, os.path.getsize(out_osgb))
        except Exception as exc:  # noqa: BLE001 —— 单瓦片失败不中断整批
            return (idx, "failed", type(exc).__name__, 0, 0, 0)
        finally:
            for f in (in_osgb, mid, out_osgt, out_osgb, errlog):
                try:
                    os.unlink(f)
                except OSError:
                    pass

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (idx, status, reason, nb, nv, bs) in pool.map(
                lambda a: one(a[0], a[1][0], a[1][1]), list(enumerate(jobs))):
            with lock_guard:
                if status == "ok":
                    stats["ok"] += 1
                    stats["blocks"] += nb
                    stats["verts"] += nv
                    stats["bytes"] += bs
                elif status == "skipped":
                    stats["skip"] += 1
                else:
                    stats["fail"] += 1
                report.append((idx, status, reason, nb, nv, bs))

    shutil.rmtree(root, ignore_errors=True)
    el = time.time() - t0
    print("=== SUMMARY ===")
    print("%d %d %d %d %d %d %.1f" % (stats["ok"], stats["skip"], stats["fail"],
                                      stats["blocks"], stats["verts"], stats["bytes"], el))
    for row in sorted(report):
        print("%d\t%s\t%s\t%d\t%d\t%d" % row)


if __name__ == "__main__":
    main(sys.argv[1])
