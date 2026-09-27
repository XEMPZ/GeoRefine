"""压力测试：200 张照片批量 POS 处理计时（验证大批量不卡死）。用后即删。"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(".")
SRC = ROOT / "sample_data" / "样例照片POS"
tmp = Path(tempfile.mkdtemp(prefix="stress_"))

# 200 张：复制样例 8 张 × 25 份到嵌套子目录
t0 = time.time()
count = 0
for k in range(25):
    sub = tmp / f"批次{k:02d}" / "子目录"
    sub.mkdir(parents=True, exist_ok=True)
    for f in SRC.rglob("*.JPG"):
        shutil.copy2(f, sub / f"IMG_{k:02d}_{count:03d}.JPG")
        count += 1
print(f"准备 {count} 张照片: {time.time()-t0:.1f}s", flush=True)

from app.common import hconv
from app.core.geoid import GridModel, load_grid

grid = load_grid(str(ROOT / "sample_data" / "局部似大地水准面格网_样例.csv"))
fn = hconv.make_photo_height_fn("geoid", model=GridModel(grid))

t0 = time.time()
res = None
from app.photo import batch as photo_batch
res = photo_batch.process(str(tmp), fn, mode_name="stress")
t1 = time.time()
ok, total = res["ok"], res["total"]
print(f"批处理 {total} 张: {t1-t0:.1f}s ({(t1-t0)/max(total,1)*1000:.0f} ms/张), ok={ok}, failed={res['failed']}", flush=True)
assert total == count and ok == count, (total, ok, count)

rb = photo_batch.rollback(str(tmp))
t2 = time.time()
print(f"回滚 {rb['restored']} 张: {t2-t1:.1f}s", flush=True)
assert rb["restored"] == count

shutil.rmtree(tmp, ignore_errors=True)
print(f"STRESS OK — 200 级照片批处理+回滚全链路 {(t2-t0):.1f}s，单张均 {(t1-t0)/count*1000:.0f} ms", flush=True)
