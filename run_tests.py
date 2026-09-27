"""通用测试 runner：发现并执行 tests/test_*.py 的 run()，汇总 PASS/FAIL。

用法: $env:PYTHONIOENCODING='utf-8'; python run_tests.py
"""
from __future__ import annotations

import importlib
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    test_files = sorted((ROOT / "tests").glob("test_*.py"))
    results = []
    for tf in test_files:
        mod_name = f"tests.{tf.stem}"
        t0 = time.time()
        try:
            mod = importlib.import_module(mod_name)
            if hasattr(mod, "run"):
                n = mod.run()
                results.append((tf.name, "PASS", n, time.time() - t0, ""))
            else:
                results.append((tf.name, "SKIP", 0, 0.0, "无 run()"))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            results.append((tf.name, "FAIL", 0, time.time() - t0, str(e)))
    print("\n===== 测试汇总 =====")
    failed = 0
    for name, status, n, dt, err in results:
        mark = {"PASS": "[PASS]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
        line = f"{mark} {name:<28} {n} checks  {dt:.1f}s"
        if err:
            line += f"  {err}"
        print(line)
        if status == "FAIL":
            failed += 1
    total = len(results)
    print(f"\n{total - failed}/{total} test files passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
