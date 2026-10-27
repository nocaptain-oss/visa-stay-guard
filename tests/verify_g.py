#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G 系列：输入容错与自然年口径（实战问答流程打磨）

覆盖 2026-10-21 一轮漏洞扫描修掉的问题：
  - 日期写法千奇百怪（用户口语 → 模型转写经常不是 ISO）不能崩
  - 国家标识传中文名/别名（日本、UK、东京）必须命中，否则会被误判成「未收录」
  - 自然年模式漏填年度上限会让限额整段失效（偏危险方向）
  - as_of 误填历史日期、已离境用户问「下次能待多久」的措辞

用法: python3 tests/verify_g.py
"""
import json, os, subprocess, sys
from _support import with_supported_profile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALC = os.path.join(ROOT, "scripts", "stay_calc.py")
LOOK = os.path.join(ROOT, "scripts", "visa_lookup.py")

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)


def calc(payload):
    payload = with_supported_profile(payload)
    r = subprocess.run(["python3", CALC], input=json.dumps(payload),
                       capture_output=True, text=True)
    return json.loads(r.stdout)   # 崩溃会在这里抛，等价于测试失败


def look(*args):
    r = subprocess.run(["python3", LOOK, *args], capture_output=True, text=True)
    return json.loads(r.stdout)


JP = {"country": "JP", "today": "2026-03-01",
      "rules": {"stay_single": 15, "stay_window": 0, "stay_window_max": 0},
      "trips": [{"country": "JP", "entry": "2026-02-20", "exit": None}]}

print("== G1 日期写法容错（同一场景，五种写法必须同结果） ==")
base = calc(JP).get("bottom_line")
for fmt in ("2026-03-01", "2026/3/1", "2026.3.1", "2026年3月1日", "20260301"):
    o = calc({**JP, "today": fmt})
    check(f"G1 {fmt}", o.get("today") == "2026-03-01" and o.get("bottom_line") == base,
          f"today={o.get('today')}")

print("== G2 无法解析的日期 → JSON 错误而不是崩栈 ==")
for bad in ("3月1日", "下个月", "月底"):
    o = calc({**JP, "today": bad})
    check(f"G2 {bad!r} 返回 error", o.get("status") == "error" and o.get("hint"),
          f"status={o.get('status')}")

print("== G3 as_of 早于 today 必须告警 ==")
o = calc({**JP, "today": "2026-10-21", "as_of": "2026-10-01", "trips": []})
check("G3 有「时间基准错位」告警",
      any("as_of" in a and "早于" in a for a in o.get("alerts", [])), f"alerts={o.get('alerts')}")
check("G3 时间基准错位必须待确认",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None,
      f"status={o.get('status')} bottom_line={o.get('bottom_line')!r}")

print("== G4 自然年模式漏填年度上限必须告警 ==")
o = calc({"country": "IN", "today": "2026-10-21",
          "rules": {"stay_single": 90, "stay_window": 0, "stay_window_max": 0,
                    "calendar_year_window": True},
          "trips": [{"country": "IN", "entry": "2026-09-01", "exit": None}]})
check("G4 报「年度额度限制不生效」",
      any("年度额度限制不生效" in a for a in o.get("alerts", [])), f"alerts={o.get('alerts')}")
check("G4b 规则疑点不得直接播报",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None)

print("== G5 自然年 + stay_window=365 是等价写法，不该报疑点 ==")
o = calc({"country": "IN", "today": "2026-10-21", "visa_source": "aggregated",
          "rules": {"stay_single": 90, "stay_window": 365, "stay_window_max": 180,
                    "calendar_year_window": True},
          "trips": [{"country": "IN", "entry": "2026-02-01", "exit": "2026-03-31"},
                    {"country": "IN", "entry": "2026-09-01", "exit": None}]})
check("G5 无录入疑点", not any("疑点" in a for a in o.get("alerts", [])), f"alerts={o.get('alerts')}")
check("G5 年度窗口生效", (o.get("window") or {}).get("mode") == "calendar_year"
      and (o.get("window") or {}).get("cap") == 180, f"window={o.get('window')}")

print("== G6 已离境用户问「下次能待多久」措辞 ==")
o = calc({**JP, "today": "2026-04-17",
          "trips": [{"country": "JP", "entry": "2026-04-17", "exit": "2026-04-17"}]})
check("G6 用「若今天入境」而非「计划入境」",
      "若今天" in (o.get("bottom_line") or "") and "计划" not in (o.get("bottom_line") or ""),
      f"got={o.get('bottom_line')!r}")

print("== G7 国家标识：中文名 / 别名 / 小写代码 ==")
for raw, want in (("日本", "JP"), ("东京", "JP"), ("UK", "GB"), ("uk", "GB"),
                  ("英国", "GB"), ("迪拜", "AE"), ("香港", "HK"), ("us", "US")):
    d = look("--rules", raw)
    check(f"G7 {raw} → {want}", d.get("countryCode") == want and d.get("found") is True,
          f"got={d.get('countryCode')} found={d.get('found')}")

print("== G8 found:false 必须带 hint（防止模型把 false 当「没有规则」） ==")
d = look("--rules", "申根")
check("G8 rules 未命中带 hint", d.get("found") is False and d.get("hint"), f"got={d}")
d = look("--types", "JP2")
check("G8 types 未命中带 hint", d.get("found") is False and d.get("hint"), f"got={d}")

print("== G9 两位 ISO 代码不被别名抢走（全量） ==")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _seal
import visa_lookup as v
bad = []
for e in _seal.load("rules-data")["entries"]:
    cc = e["countryCode"]
    if len(cc) == 2 and cc.isascii() and cc.isalpha() and v.resolve_cc(cc) != cc:
        bad.append((cc, v.resolve_cc(cc)))
check("G9 全部代码原样返回", not bad, f"被抢走={bad}")

print()
print(f"结果: {len(fails)} 失败" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
