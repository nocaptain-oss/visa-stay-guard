#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：身份门禁、确认状态、日期基准、逐日窗口与同池连续停留。"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "stay_calc.py")


def run(payload):
    proc = subprocess.run(
        ["python3", SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr[:300]
    return json.loads(proc.stdout)


fails = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'} {name}  {detail}")
    if not condition:
        fails.append(name)


base = {
    "country": "JP",
    "today": "2026-10-21",
    "rules": {"stay_single": 15, "stay_window": 0, "stay_window_max": 0},
    "trips": [{"country": "JP", "entry": "2026-10-01", "exit": None}],
}

out = run({
    **base,
    "passport_nationality": "US",
    "passport_type": "ordinary",
    "residence_status": "student",
})
check("R1a 非适用身份必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R1b 非适用身份不得给结论", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")
check("R1c 必须提出身份确认问题", bool(out.get("questions")), f"questions={out.get('questions')}")

out = run(base)
check("R1d 缺少身份不得套默认规则",
      out.get("status") == "needs_confirmation" and out.get("bottom_line") is None)

supported = {
    **base,
    "passport_nationality": "CN",
    "passport_type": "ordinary",
    "residence_status": "visitor",
}

missing_today = dict(supported)
missing_today.pop("today")
out = run(missing_today)
check("R2a today 缺失必须报错", out.get("status") == "error", f"status={out.get('status')}")
check("R2b today 缺失不得静默用主机日期", out.get("today") is None, f"today={out.get('today')!r}")

out = run({**supported, "as_of": ""})
check("R2c as_of 空字符串必须报错", out.get("status") == "error", f"status={out.get('status')}")

shanghai = run({
    **supported,
    "today": "2026-10-27",
    "trips": [{"country": "JP", "entry": "2026-10-20", "exit": None}],
})
tokyo = run({
    **supported,
    "today": "2026-10-28",
    "trips": [{"country": "JP", "entry": "2026-10-20", "exit": None}],
})
check("R2d 上海日期剩余 7 天", (shanghai.get("single") or {}).get("remaining_days") == 7, f"got={shanghai.get('single')}")
check("R2e 东京日期剩余 6 天", (tokyo.get("single") or {}).get("remaining_days") == 6, f"got={tokyo.get('single')}")

out = run({
    **supported,
    "trips": [{"country": "JP", "entry": "18号", "exit": None}],
})
check("R3a 推定日期必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R3b 推定日期不得给 bottom_line", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")
check("R3c 推定日期不得暴露 single 结论", out.get("single") is None, f"got={out.get('single')!r}")

es = {
    **supported,
    "country": "ES",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [
        {"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"},
        {"country": "ES", "entry": "2026-09-11", "exit": None},
    ],
    "reported_days": 30,
}
out = run(es)
check("R3d 口述天数冲突必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R3e 口述天数冲突不得给 bottom_line", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")

out = run({
    **supported,
    "country": "ES",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [{"country": "FR", "entry": "2026-09-20", "exit": None}],
})
check("R4a 同池他国在途不得说今天入境", "若今天" not in (out.get("bottom_line") or ""), f"got={out.get('bottom_line')!r}")
check("R4b 同池他国在途应识别当前池", out.get("current_pool") == "SCHENGEN", f"got={out.get('current_pool')}")
check("R4c 同池他国在途保留真实单次起点", (out.get("single") or {}).get("entry") == "2026-09-20", f"got={out.get('single')}")

out = run({
    **supported,
    "country": "ES",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [
        {"country": "ES", "entry": "2026-09-01", "exit": "2026-09-20"},
        {"country": "FR", "entry": "2026-09-21", "exit": None},
    ],
})
check("R5a 连续同池停留不得重置单次起点", (out.get("single") or {}).get("entry") == "2026-09-01", f"got={out.get('single')}")
check("R5b 连续同池停留保持真实剩余天数", (out.get("single") or {}).get("remaining_days") == 39, f"got={out.get('single')}")

out = run({
    **supported,
    "country": "ES",
    "today": "2026-06-01",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [
        {"country": "ES", "entry": "2026-01-01", "exit": "2026-01-30"},
        {"country": "ES", "entry": "2026-06-01", "exit": None},
    ],
})
check("R6a 滚动窗口必须模拟逐日释放", (out.get("single") or {}).get("last_allowed_day") == "2026-08-29", f"got={out.get('single')}")
check("R6b 连续可待天数应为 90 天", (out.get("single") or {}).get("effective_days") == 90, f"got={out.get('single')}")

out = run({
    **supported,
    "country": "ES",
    "today": "2026-11-01",
    "as_of": "2026-12-01",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [
        {"country": "FR", "entry": "2026-06-01", "exit": "2026-07-31"},
        {"country": "FR", "entry": "2026-07-01", "exit": "2026-10-01"},
        {"country": "ES", "entry": "2026-10-02", "exit": "2026-10-31"},
    ],
})
check("R7a 窗口耗尽必须 blocked", out.get("status") == "blocked", f"status={out.get('status')}")
check("R7b 窗口耗尽不得返回 1 天", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")
check("R7c 窗口耗尽额度必须为 0", (out.get("single") or {}).get("effective_days") == 0, f"got={out.get('single')}")

out = run({
    **supported,
    "trips": [{"country": "JP", "entry": "2026-11-01", "exit": None}],
})
check("R8a 未来 open trip 必须进入 planning", out.get("status") == "planning", f"status={out.get('status')}")
check("R8b 未来 open trip 保留计划入境日", (out.get("single") or {}).get("entry") == "2026-11-01", f"got={out.get('single')}")

out = run({
    **supported,
    "country": "ES",
    "as_of": "2026-12-01",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [{"country": "ES", "entry": "2026-09-20", "exit": None}],
})
check("R9a 当前在途加未来 as_of 必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R9b 混合场景不得给 bottom_line", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")

out = run({
    **supported,
    "rules": {"stay_single": 15, "stay_window": 365, "stay_window_max": 30,
              "calendar_year_window": "false"},
})
check("R10 字符串 false 必须报错", out.get("status") == "error", f"status={out.get('status')}")

out = run({
    **supported,
    "visa_source": "web",
})
check("R11a web 来源缺失必须报错", out.get("status") == "error", f"status={out.get('status')}")

out = run({
    **supported,
    "visa_source": "user_rules",
    "rules": {"stay_single": 180, "stay_window": 90, "stay_window_max": 90},
})
check("R12a 规则录入疑点必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R12b 规则录入疑点不得给 bottom_line", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")

out = run({
    **supported,
    "trips": [{"country": "JP", "entry": "2026-11-01", "exit": "2026-12-01"}],
})
check("R13a 未来已闭行程必须待确认", out.get("status") == "needs_confirmation", f"status={out.get('status')}")
check("R13b 未来已闭行程不得给 bottom_line", out.get("bottom_line") is None, f"got={out.get('bottom_line')!r}")

out = run({
    **supported,
    "country": "ES",
    "as_of": "2026-12-17",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [
        {"country": "FR", "entry": "2026-08-01", "exit": "2026-09-10"},
        {"country": "ES", "entry": "2026-09-16", "exit": "2026-10-20"},
    ],
})
check("R14a 本次停留天数必须累计进窗口", (out.get("single") or {}).get("effective_days") == 14, f"got={out.get('single')}")
check("R14b 静态历史占用下最晚离境日应为 2026-12-30", (out.get("single") or {}).get("last_allowed_day") == "2026-12-30", f"got={out.get('single')}")

out = run({**supported, "reported_days": "约十天"})
check("R15 非数字口述天数必须报错", out.get("status") == "error", f"status={out.get('status')}")

out = run({**supported, "reported_days": 30.5})
check("R15b 小数口述天数必须报错", out.get("status") == "error", f"status={out.get('status')}")

out = run({**supported, "visa_source": "guess"})
check("R16 未知签证来源必须报错", out.get("status") == "error", f"status={out.get('status')}")

out = run({
    **supported,
    "visa_source": "web",
    "web_source": {"name": "官方页", "url": "https://example.gov", "date": "2026-10-21"},
    "rules_source": "aggregated",
})
check("R17 rules_source 与 visa_source 冲突必须报错", out.get("status") == "error", f"status={out.get('status')}")

print(f"\n{len(fails)} 失败")
sys.exit(1 if fails else 0)
