#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 系列：播报事故修复验证（红绿两用）

用法:
  python3 tests/verify_fix.py                    # 测包内 scripts/stay_calc.py
  python3 tests/verify_fix.py --old /path/to/old.py   # 测旧版（红绿对照：必须失败）
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_args = [a for a in sys.argv[1:] if a != "--old"]
SCRIPT = _args[0] if _args else os.path.join(ROOT, "scripts", "stay_calc.py")

def run(payload):
    r = subprocess.run(["python3", SCRIPT], input=json.dumps(payload), capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"脚本退出码 {r.returncode}: {r.stderr[:300]}")
    return json.loads(r.stdout)

BASE = {
  "country": "ES", "today": "2026-10-21",
  "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
  "trips": [
    {"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"},
    {"country": "ES", "entry": "2026-09-11", "exit": None}
  ],
  "visa_type": "旅游签"
}

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

print(f"== 被测脚本: {SCRIPT} ==")

# T1 原始事故场景：口述 60 vs 实算 82 + 播报数字
o = run({**BASE, "reported_days": 60})
check("T1a bottom_line 数字=8 天", o.get("bottom_line") == "最晚离境日 2026-10-29，还能再待 8 天（不含今天）", f"got={o.get('bottom_line')!r}")
check("T1b 口径差异 alert 存在", any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")
check("T1c window.used=82", o["window"]["used"] == 82, f"got={o['window']['used']}")
check("T1d remaining=8(窗口)", o["window"]["remaining"] == 8, f"got={o['window']['remaining']}")
check("T1e single.remaining_days=8", o["single"]["remaining_days"] == 8, f"got={o['single']['remaining_days']}")

# T2 口述与实算一致：不误报
o = run({**BASE, "reported_days": 82})
check("T2 一致时不报口径差异", not any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")

# T3 非数字容错：不崩、不误报
o = run({**BASE, "reported_days": "abc"})
check("T3a 非数字不崩", o.get("status") in ("normal", "near", "danger"), f"status={o.get('status')}")
check("T3b 非数字不报口径差异", not any("口径差异" in a for a in o["alerts"]))

# T4 无窗口国家（JP 口径）+ 口述冲突：scope=全部行程
o = run({
  "country": "JP", "today": "2026-10-21",
  "rules": {"stay_single": 15, "stay_window": 0, "stay_window_max": 0},
  "trips": [{"country": "JP", "entry": "2026-10-01", "exit": None}],
  "reported_days": 15
})
check("T4 无窗口国家口径差异可检出", any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")

# T5 事故反演（漏传 FR 行程时的自愈提示）：单传 ES 在途
o = run({**BASE, "trips": [BASE["trips"][1]], "reported_days": 60})
check("T5 漏传行程时口径差异仍触发(used=41 vs 60)", any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")

# T6 planning 场景不崩
o = run({
  "country": "ES", "today": "2026-10-21", "as_of": "2026-12-01",
  "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
  "trips": [{"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"}]
})
check("T6 planning bottom_line 非空", bool(o.get("bottom_line")), f"got={o.get('bottom_line')!r}")

print(f"\n结果: {len(fails)} 失败")
sys.exit(1 if fails else 0)
