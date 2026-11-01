#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 系列：播报事故修复验证（红绿两用）

用法:
  python3 tests/verify_fix.py                    # 测包内 scripts/stay_calc.py
  python3 tests/verify_fix.py --old /path/to/old.py   # 测旧版（红绿对照：必须失败）
"""
import json, os, subprocess, sys
from _support import with_supported_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_args = [a for a in sys.argv[1:] if a != "--old"]
SCRIPT = _args[0] if _args else os.path.join(ROOT, "scripts", "stay_calc.py")

def run(payload):
    payload = with_supported_profile(payload)
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
check("T1a 口径冲突不得直接播报", o.get("status") == "needs_confirmation" and o.get("bottom_line") is None, f"status={o.get('status')}")
check("T1b 口径差异 alert 存在", any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")
candidate = o.get("candidate") or {}
check("T1c window.used=82", candidate["window"]["used"] == 82, f"got={candidate['window']['used']}")
check("T1d remaining=8(窗口)", candidate["window"]["remaining"] == 8, f"got={candidate['window']['remaining']}")
check("T1e single.remaining_days=8", candidate["single"]["remaining_days"] == 8, f"got={candidate['single']['remaining_days']}")

# T2 口述与实算一致：不误报
o = run({**BASE, "reported_days": 82})
check("T2 一致时不报口径差异", not any("口径差异" in a for a in o["alerts"]), f"alerts={o['alerts']}")

# T3 非数字口述天数：结构化报错，不静默忽略
o = run({**BASE, "reported_days": "abc"})
check("T3a 非数字返回结构化错误", o.get("status") == "error" and o.get("hint"), f"status={o.get('status')}")
check("T3b 非数字不误报口径差异", not any("口径差异" in a for a in o.get("alerts", [])))

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
check("T5b 漏传行程冲突时不得播报", o.get("status") == "needs_confirmation" and o.get("bottom_line") is None)

# T6 planning 场景不崩
o = run({
  "country": "ES", "today": "2026-10-21", "as_of": "2026-12-01",
  "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
  "trips": [{"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"}]
})
check("T6 planning bottom_line 非空", bool(o.get("bottom_line")), f"got={o.get('bottom_line')!r}")

# T7 单次上限 ≠ 窗口上限：同池跨境连续停留必须按池合并、受单次上限约束
# （对抗报告 M-2 点名的唯一「建模错误→数值错误」路径）
o = run({
  "country": "FR", "today": "2026-10-21",
  "rules": {"stay_single": 30, "stay_window": 180, "stay_window_max": 90},
  "trips": [
    {"country": "ES", "entry": "2026-09-01", "exit": "2026-09-20"},
    {"country": "FR", "entry": "2026-09-21", "exit": None}
  ]
})
s7 = o.get("single") or {}
check("T7a 连续停留从池内首日起算", s7.get("entry") == "2026-09-01", f"got={s7.get('entry')}")
check("T7b 单次上限 30 生效（非窗口 90）", s7.get("effective_days") == 30, f"got={s7.get('effective_days')}")
check("T7c 超单次上限→overdue 且最晚 09-30",
      o.get("status") == "overdue" and s7.get("last_allowed_day") == "2026-09-30",
      f"status={o.get('status')} last={s7.get('last_allowed_day')}")

# T8 confirmed 放行软性疑点（口径差异）
o = run({**BASE, "reported_days": 60, "confirmed": True})
check("T8a confirmed 放行口径差异", o.get("status") != "needs_confirmation" and bool(o.get("bottom_line")),
      f"status={o.get('status')} bl={o.get('bottom_line')!r}")
check("T8b 放行后仍保留差异 alert", any("口径差异" in a for a in o.get("alerts", [])))

# T9 confirmed 不放行硬冲突（as_of 与在途行程冲突：必须改输入）
o = run({
  "country": "ES", "today": "2026-10-21", "as_of": "2026-12-01", "confirmed": True,
  "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
  "trips": [{"country": "ES", "entry": "2026-09-20", "exit": None}]
})
check("T9 as_of 冲突不被 confirmed 放行",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None,
      f"status={o.get('status')}")

# T10 confirmed 类型严格（吸取 P1-1 字符串 bool 教训）
o = run({**BASE, "reported_days": 60, "confirmed": "true"})
check("T10 字符串 confirmed 返回结构化错误", o.get("status") == "error" and o.get("hint"), f"status={o.get('status')}")

print(f"\n结果: {len(fails)} 失败")
sys.exit(1 if fails else 0)
