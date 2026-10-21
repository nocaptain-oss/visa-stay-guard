#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""13 场景回归：tests/fixtures/ 下 t1-t12 + th.json 全量跑通

不变量：bottom_line 里的“再待 N 天”必须与 single.remaining_days 一致（非 planning、非逾期）。

用法: python3 tests/regress.py [被测脚本路径]
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "scripts", "stay_calc.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

files = [os.path.join(FIXTURES, f"t{i}.json") for i in range(1, 13)]
files.append(os.path.join(FIXTURES, "th.json"))

fails = []
for f in files:
    r = subprocess.run(["python3", SCRIPT, f], capture_output=True, text=True)
    if r.returncode != 0:
        fails.append((f, f"exit {r.returncode}: {r.stderr[:200]}"))
        print(f"FAIL {os.path.basename(f):8s} exit {r.returncode}")
        continue
    o = json.loads(r.stdout)
    bl = o.get("bottom_line") or ""
    ok_bl = bool(bl) and ("离境" in bl or "待" in bl or "额度" in bl or "无法计算" in bl)
    inv = True
    if o.get("single") and o["status"] != "planning" and not o["single"].get("overdue"):
        rm = o["single"]["remaining_days"]
        inv = (f"再待 {rm} 天" in bl)
    if not (ok_bl and inv):
        fails.append((f, f"bl={bl!r} inv={inv}"))
        print(f"FAIL {os.path.basename(f):8s} [{o['status']:8s}] bl={bl!r} inv={inv}")
    else:
        print(f"OK   {os.path.basename(f):8s} [{o['status']:8s}] {bl}")

print(f"\n回归结果: {len(fails)} 失败 / {len(files)} 场景")
for f, m in fails:
    print(f"  FAIL {os.path.basename(f)}: {m}")
sys.exit(1 if fails else 0)
