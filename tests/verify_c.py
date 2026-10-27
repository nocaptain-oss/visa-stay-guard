#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 系列：行程池过滤（same_pool）验证

用法: python3 tests/verify_c.py [被测脚本路径]
"""
import json, os, subprocess, sys
from _support import with_supported_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "scripts", "stay_calc.py")

def run(p):
    p = with_supported_profile(p)
    r = subprocess.run(["python3", SCRIPT], input=json.dumps(p), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)

TH_TRIPS = [
    {"country": "TH", "entry": "2026-10-01", "exit": "2026-10-09"},
    {"country": "GB", "entry": "2026-10-10", "exit": "2026-10-13"},
    {"country": "TH", "entry": "2026-10-14", "exit": None}
]
fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

# C1: 无池国家剔除池外行程（GB 4 天不算）
o = run({"country": "TH", "today": "2026-10-21",
         "rules": {"stay_single": 30, "stay_window": 180, "stay_window_max": 90},
         "trips": TH_TRIPS, "reported_days": 21})
candidate = o.get("candidate") or {}
check("C1a TH 窗口 used=17(剔 GB)", candidate["window"]["used"] == 17, f"got={candidate['window']['used']}")
check("C1b 口径差异 21 vs 17 触发", any("口径差异" in a for a in o["alerts"]))
check("C1c single eff=30 不受 GB 污染", candidate["single"]["effective_days"] == 30, f"got={candidate['single']['effective_days']}")

# C1 换威胁场景：池外大额行程不挤占本国额度
o = run({"country": "TH", "today": "2026-10-21",
         "rules": {"stay_single": 30, "stay_window": 180, "stay_window_max": 90},
         "trips": [{"country": "GB", "entry": "2026-04-01", "exit": "2026-09-30"},
                   {"country": "TH", "entry": "2026-10-14", "exit": None}]})
check("C1d GB 183 天不进 TH 窗口(used=8)", o["window"]["used"] == 8, f"got={o['window']['used']}")

# C2: 无窗口国家 planning 不带封顶注
o = run({"country": "JP", "today": "2026-10-21", "as_of": "2027-01-15",
         "rules": {"stay_single": 15, "stay_window": 0, "stay_window_max": 0},
         "trips": [], "visa_days": 90})
check("C2 JP planning 无'封顶'注", "封顶" not in o["bottom_line"], f"bl={o['bottom_line']!r}")
# 对照：有窗口国家 planning 保留封顶注
o = run({"country": "ES", "today": "2026-10-21", "as_of": "2026-12-17",
         "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
         "trips": [{"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"},
                    {"country": "ES", "entry": "2026-09-16", "exit": "2026-10-20"}]})
check("C2b ES planning 保留'封顶'注", "封顶" in o["bottom_line"], f"bl={o['bottom_line']!r}")

# 申根池过滤不回归：GB 混进 ES 计算仍被剔
o = run({"country": "ES", "today": "2026-10-21",
         "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
         "trips": [{"country": "GB", "entry": "2026-10-10", "exit": "2026-10-13"},
                    {"country": "ES", "entry": "2026-09-16", "exit": None}]})
check("C3 ES 池过滤 used=36(剔 GB)", o["window"]["used"] == 36, f"got={o['window']['used']}")

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
