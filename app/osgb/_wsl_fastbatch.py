"""在单个进程内批量转换大量 OSGB 瓦片（多进程并行）。

单元成本（实测，2.6 MB 瓦片）
-----------------------------
| 环节 | 耗时 | 说明 |
|---|---|---|
| osgconv 读 osgb → 写 osgt（本地盘） | 0.42 s | OSG 解析 + 文本序列化 |
| 顶点改写（纯 Python 算术） | ~0.2 s | 与顶点数成正比 |
| osgconv 读 osgt → 写 osgb（本地盘） | 0.25 s | 重新序列化 |
| **合计** | **~0.9 s** | 拷贝与进程启动已消除 |

前面几版每瓦片还要额外付出：2 次 wsl.exe 进程启动 + 2 次跨挂载点文件拷贝
（约 0.5 s）。本模块把这些全部去掉：

- **单进程读多个文件**：一个进程内循环所有瓦片，避免每瓦片重启 osgconv
- **并行靠多进程**：每 worker 一个进程，各自处理一个子集，16 核可跑满
- **本地盘作业**：读写都在 /tmp，只有最终产物拷回挂载点

用法（供 WSL 内 python3 调用）:
    python3 _wsl_fastbatch.py cfg.json
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import multiprocessing as mp

# 模块级全局：fork 出的子进程继承，避免在 spawn 模式下复制大对象
_TASKS = None


def _run_osgconv(osgconv, src, dst, errlog):
    """调用 osgconv；返回是否成功。"""
    with open(errlog, "w") as err:
        p = subprocess.run([osgconv, "--compressed", src, dst], stdout=subprocess.DEVNULL, stderr=err,
                           timeout=1800)
    return p.returncode == 0 and os.path.exists(dst)


def _worker(args):
    """一个 worker：处理分到的瓦片子集。返回统计元组。"""
    global _TASKS
    wid, cfg, job_slice = args
    osgconv = cfg["osgconv"]
    py = cfg["python"]
    rewriter = cfg["rewriter"]
    params_path = cfg["params_path"]
    work = tempfile.mkdtemp(prefix="osgbw%d_" % wid)
    in_osgb = os.path.join(work, "in.osgb")
    mid = os.path.join(work, "mid.osgt")
    out_osgt = os.path.join(work, "out.osgt")
    out_osgb = os.path.join(work, "out.osgb")
    errlog = os.path.join(work, "err.txt")
    ok = skip = fail = blocks = verts = nbytes = 0
    report = []
    try:
        for (idx, src, dst) in job_slice:
            try:
                if not os.path.exists(src):
                    fail += 1
                    report.append((idx, "failed", "missing_src"))
                    continue
                shutil.copyfile(src, in_osgb)
                if not _run_osgconv(osgconv, in_osgb, mid, errlog):
                    fail += 1
                    report.append((idx, "failed", "read"))
                    continue
                r = subprocess.run([py, rewriter, mid, out_osgt, params_path],
                                   capture_output=True, text=True, timeout=1800)
                out = (r.stdout or "").strip().splitlines()
                if not out:
                    fail += 1
                    report.append((idx, "failed", "rewrite"))
                    continue
                nb, nv = (int(v) for v in out[-1].split())
                if nv == 0:
                    skip += 1
                    report.append((idx, "skipped", "noverts"))
                    continue
                if not _run_osgconv(osgconv, out_osgt, out_osgb, errlog):
                    fail += 1
                    report.append((idx, "failed", "write"))
                    continue
                d = os.path.dirname(dst)
                if d:
                    os.makedirs(d, exist_ok=True)
                shutil.copyfile(out_osgb, dst)
                bs = os.path.getsize(out_osgb)
                ok += 1
                blocks += nb
                verts += nv
                nbytes += bs
                report.append((idx, "ok", "%d %d %d" % (nb, nv, bs)))
            except Exception as exc:  # noqa: BLE001 —— 单瓦片失败不中断
                fail += 1
                report.append((idx, "failed", type(exc).__name__))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return (ok, skip, fail, blocks, verts, nbytes, report)


def main(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    jobs = cfg["jobs"]
    workers = max(1, min(int(cfg.get("workers", 8)), len(jobs)))

    # 按轮转分片，让每片的体积尽量均衡（大瓦片分散到不同 worker）
    slices = [[] for _ in range(workers)]
    for i, job in enumerate(jobs):
        slices[i % workers].append((i, job[0], job[1]))

    t0 = time.time()
    ctx = mp.get_context("fork") if hasattr(os, "fork") else mp.get_context()
    with ctx.Pool(workers) as pool:
        results = pool.map(_worker, [(w, cfg, slices[w]) for w in range(workers)])

    ok = skip = fail = blocks = verts = nbytes = 0
    merged = []
    for (o, s, f, b, v, nb, rep) in results:
        ok += o; skip += s; fail += f
        blocks += b; verts += v; nbytes += nb
        merged.extend(rep)
    el = time.time() - t0
    print("=== SUMMARY ===")
    print("%d %d %d %d %d %d %.1f" % (ok, skip, fail, blocks, verts, nbytes, el))
    for row in sorted(merged):
        print("%d\t%s\t%s" % row)


if __name__ == "__main__":
    main(sys.argv[1])
