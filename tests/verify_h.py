#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H 系列：农历换算与汉字数字日期

用户口语里「五月初五」「农历八月十五」「五月十八」混着出现，且前两种只可能是农历
（公历没有「初五」这种说法）。本轮规则：
  - 带「农历/阴历」、含「闰X月」、含「初X」→ 必按农历换算
  - 其余 → 当公历，但汉字数字要归一（「五月十八」= 5 月 18 日）
  - 只说「初五」没有月份 → 报错追问，不猜

数据向量的外部核对来源：黄历工具（2026 春节 2026-02-17、中秋 2026-09-25）
与 zhdate 参考实现（1900-2100 逐日 73372 天零误差）。

用法: python3 tests/verify_h.py
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _lunar as L
from stay_calc import d, InputError

CALC = os.path.join(ROOT, "scripts", "stay_calc.py")
fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


def calc(payload):
    r = subprocess.run(["python3", CALC], input=json.dumps(payload),
                       capture_output=True, text=True)
    return json.loads(r.stdout)


def iso(y, m, dd, leap=False):
    v = L.lunar_to_solar(y, m, dd, leap)
    return v.isoformat() if v else None


print("== H1 已知锚点（外部核对） ==")
check("H1.1 2026 正月初一 = 2026-02-17", iso(2026, 1, 1) == "2026-02-17")
check("H1.2 2026 八月十五 = 2026-09-25", iso(2026, 8, 15) == "2026-09-25")
check("H1.3 2025 闰六月初一 = 2025-07-25", iso(2025, 6, 1, True) == "2025-07-25")
check("H1.4 2033 闰十一月初一 = 2033-12-22", iso(2033, 11, 1, True) == "2033-12-22")
check("H1.5 1900 正月初一 = 1900-01-31", iso(1900, 1, 1) == "1900-01-31")
check("H1.6 2100 正月初一 = 2100-02-09", iso(2100, 1, 1) == "2100-02-09")

print("== H2 闰月与越界 ==")
check("H2.1 2026 无闰月", L.leap_month_of(2026) == 0)
check("H2.2 2025 闰六月", L.leap_month_of(2025) == 6)
check("H2.3 无此闰月返回 None", iso(2026, 5, 1, True) is None)
check("H2.4 1899 越界", iso(1899, 1, 1) is None)
check("H2.5 2101 越界", iso(2101, 1, 1) is None)
# 2026 年农历二月是否有三十，按数据判断，不写死
feb30 = iso(2026, 2, 30)
check("H2.6 不存在的小月三十返回 None 或真实日期",
      feb30 is None or feb30.endswith("-30"), f"二月初十={iso(2026,2,10)} 二月三十={feb30}")

print("== H3 汉字数字与农历判定 ==")
check("H3.1 农历八月十五 → 2026-09-25",
      L.parse_lunar("农历八月十五", 2026)[0] == L.lunar_to_solar(2026, 8, 15))
check("H3.2 五月初五 含「初」→ 必是农历", L.is_lunar("五月初五"))
check("H3.3 初五（无月份）无法定位", L.parse_lunar("初五", 2026)[0] is None)
check("H3.4 八月十五 无前缀 → 不是农历（按用户规则）", not L.is_lunar("八月十五"))
check("H3.5 五月十八 无前缀 → 不是农历", not L.is_lunar("五月十八"))
check("H3.6 闰二月十五 → 2023-04-05",
      L.parse_lunar("2023年闰二月十五", 2030)[0].isoformat() == "2023-04-05")
check("H3.7 腊月廿三 → 公历跨年", L.parse_lunar("腊月廿三", 2026)[0].isoformat() == "2027-01-30")
check("H3.8 正月初一 = 春节",
      L.parse_lunar("正月初一", 2026)[0].isoformat() == "2026-02-17")
check("H3.9 汉字数字：十五=15 廿三=23 三十=30",
      (L.cn_num("十五"), L.cn_num("廿三"), L.cn_num("三十")) == (15, 23, 30))

print("== H4 d() 入口 ==")
check("H4.1 2026年农历八月十五 → 2026-09-25", d("2026年农历八月十五").isoformat() == "2026-09-25")
try:
    d("2026年八月十五")
    check("H4.2 无后缀汉字日 → 历法追问", False, "没有抛错")
except InputError as e:
    check("H4.2 无后缀汉字日 → 历法追问（hint 含公历农历）",
          "公历" in (e.hint or "") and "农历" in (e.hint or ""), (e.hint or "")[:30])
check("H4.2b 带号后缀按公历：2026年八月十五号 → 2026-08-15",
      d("2026年八月十五号").isoformat() == "2026-08-15")
check("H4.3 阿拉伯日无后缀仍公历：2026年5月18 → 2026-05-18",
      d("2026年5月18").isoformat() == "2026-05-18")
check("H4.4 2026年五月初五 → 农历", d("2026年五月初五").isoformat() == "2026-06-19")
try:
    d("初五")
    check("H4.5 只说初五 → 拒绝并追问", False, "没有抛错")
except InputError as e:
    check("H4.5 只说初五 → 拒绝并追问", "月" in (e.hint or ""), (e.hint or "")[:24])
try:
    d("五月十八")
    check("H4.6 无后缀无年份 → 历法追问优先", False, "没有抛错")
except InputError as e:
    check("H4.6 无后缀无年份 → 历法追问优先（且提醒公历需补年份）",
          "公历" in (e.hint or "") and "农历" in (e.hint or "") and "年份" in (e.hint or ""),
          (e.hint or "")[:30])

print("== H5 整条计算链 ==")
base = {"rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
        "today": "2026-10-21"}
r = calc({**base, "country": "ES",
          "trips": [{"country": "ES", "entry": "2026年农历八月十五", "exit": None}]})
check("H5.1 农历入境日可算", r.get("status") != "error", r.get("bottom_line", r.get("hint", ""))[:40])
r2 = calc({**base, "country": "ES",
           "trips": [{"country": "ES", "entry": "2026-09-25", "exit": None}]})
check("H5.2 农历入口与显式公历结果一致",
      r.get("bottom_line") == r2.get("bottom_line"), r.get("bottom_line", ""))
r3 = calc({**base, "country": "ES", "trips": [{"country": "ES", "entry": "初五", "exit": None}]})
a3 = " ".join(r3.get("alerts", []))
check("H5.3 只说初五 → 30 天窗口推定 + 须确认 alert（不再崩栈）",
      r3.get("status") != "error" and "确认" in a3 and "缺月份" in a3, a3[:50])

print("== H6 缺年份代年：春节前腊月/冬月跨年 ==")
from datetime import date as _date
out, tag, inf = L.parse_lunar("腊月初五", near=_date(2027, 1, 15))
check("H6.1 春节前(2027-01-15)腊月初五 → 刚过去那次 2027-01-12",
      out.isoformat() == "2027-01-12" and inf is True, str(out))
check("H6.2 归属农历年 2026（非公历直代）", tag == "2026 年农历腊月初五", tag)
out2, tag2, inf2 = L.parse_lunar("腊月廿八", near=_date(2026, 1, 15))
check("H6.3 春节前(2026-01-15)腊月廿八 → 将来那次 2026-02-15",
      out2.isoformat() == "2026-02-15" and inf2 is True, str(out2))
out3, _, inf3 = L.parse_lunar("腊月初五", near=_date(2026, 10, 22))
check("H6.4 正常期(2026-10-22)腊月初五 → 2027-01-12",
      out3.isoformat() == "2027-01-12" and inf3 is True, str(out3))
out4, _, inf4 = L.parse_lunar("正月初五", near=_date(2027, 1, 15))
check("H6.5 春节前正月初五 → 即将到来的 2027-02-10",
      out4.isoformat() == "2027-02-10" and inf4 is True, str(out4))
out5, _, inf5 = L.parse_lunar("2026年腊月初五")
check("H6.6 显式年份不算代年", inf5 is False and out5.isoformat() == "2027-01-12")
th_rules = {"stay_single": 30, "stay_window": 180, "stay_window_max": 90}
r = calc({"country": "TH", "today": "2027-01-15", "rules": th_rules,
          "trips": [{"country": "TH", "entry": "腊月初五", "exit": None}]})
alert_txt = " ".join(r.get("alerts", []))
check("H6.7 缺年份代年触发「须确认」alert（含换算公历日）",
      "确认" in alert_txt and "2027-01-12" in alert_txt, alert_txt[:60])
r2 = calc({"country": "TH", "today": "2027-01-15", "rules": th_rules,
           "trips": [{"country": "TH", "entry": "2026年腊月初五", "exit": None}]})
alert2 = " ".join(r2.get("alerts", []))
check("H6.8 含年份不触发代年确认 alert", "代年" not in alert2, alert2[:40])
r3b = calc({"country": "TH", "today": "2027-01-15", "rules": th_rules,
            "trips": [{"country": "TH", "entry": "2027-01-12", "exit": None}]})
check("H6.9 代年换算与显式公历同日结果一致",
      r.get("bottom_line") == r3b.get("bottom_line"), r.get("bottom_line", "")[:40])

print("== H7 纯日号：缺月份 30 天窗口推定 ==")
import importlib
import stay_calc as SC
importlib.reload(SC)
check("H7.1 反查：2026-10-22 = 农历九月十三",
      L.solar_to_lunar(_date(2026, 10, 22)) == (2026, 9, 13, False))
check("H7.2 纯农历日名解析", (L.parse_lunar_day("初五"), L.parse_lunar_day("十五"),
      L.parse_lunar_day("廿三"), L.parse_lunar_day("初十"),
      L.parse_lunar_day("五月十八")) == (5, 15, 23, 10, None))
check("H7.3 18号 过去 → 2026-10-18",
      SC.d("18号", near=_date(2026, 10, 22)).isoformat() == "2026-10-18")
check("H7.4 十八号（汉字）→ 同 18号",
      SC.d("十八号", near=_date(2026, 10, 22)).isoformat() == "2026-10-18")
check("H7.5 18号 未来 → 2026-11-18",
      SC.d("18号", near=_date(2026, 10, 22), bias="future").isoformat() == "2026-11-18")
check("H7.6 初五 过去 → 2026-10-14（九月初五）",
      SC.d("初五", near=_date(2026, 10, 22)).isoformat() == "2026-10-14")
check("H7.7 初五 未来 → 2026-11-13（十月初五）",
      SC.d("初五", near=_date(2026, 10, 22), bias="future").isoformat() == "2026-11-13")
r = calc({"country": "TH", "today": "2026-10-22", "rules": th_rules,
          "trips": [{"country": "TH", "entry": "十八号", "exit": None}]})
a1 = " ".join(r.get("alerts", []))
check("H7.8 缺月份推定触发「须确认」alert（含推定公历日）",
      "确认" in a1 and "2026-10-18" in a1 and "缺月份" in a1, a1[:60])
r2 = calc({"country": "JP", "today": "2026-10-22", "date_bias": "future",
           "as_of": "初五", "rules": {"stay_single": 90, "stay_window": 0, "stay_window_max": 0},
           "trips": []})
a2 = " ".join(r2.get("alerts", []))
check("H7.9 date_bias=future 时初五按未来推定（2026-11-13）",
      "2026-11-13" in a2 and "未来" in a2, a2[:60])
check("H7.10 未来推定进 bottom_line 计划入境日",
      r2.get("bottom_line", "").startswith("计划 2026-11-13 入境"), r2.get("bottom_line", "")[:30])

print("== H8 「大年」口语 ==")
check("H8.1 is_lunar 认「大年」", L.is_lunar("大年三十") and L.is_lunar("大年初五"))
o, t, i = L.parse_lunar("2026年大年三十")
check("H8.2 2026年大年三十 = 除夕 2027-02-05（腊月无三十回退廿九）",
      o.isoformat() == "2027-02-05" and "廿九" in t and i is False, t)
check("H8.3 2026年大年初一 = 2026 春节 2026-02-17",
      L.parse_lunar("2026年大年初一")[0].isoformat() == "2026-02-17")
check("H8.4 2026年大年二十三 = 小年 2027-01-30（同 H3.7 腊月廿三）",
      L.parse_lunar("2026年大年二十三")[0].isoformat() == "2027-01-30")
o4, t4, i4 = L.parse_lunar("大年三十", near=_date(2026, 10, 22))
check("H8.5 缺年份大年三十 → 最近除夕 2027-02-05 + 代年须确认",
      o4.isoformat() == "2027-02-05" and i4 is True, t4)
o5, _, i5 = L.parse_lunar("大年初五", near=_date(2026, 10, 22))
check("H8.6 缺年份大年初五 → 最近将来 2027-02-10 + 代年须确认",
      o5.isoformat() == "2027-02-10" and i5 is True, str(o5))
r8 = calc({"country": "TH", "today": "2026-10-22", "rules": th_rules,
           "trips": [{"country": "TH", "entry": "2026年大年三十", "exit": None}]})
a8 = " ".join(r8.get("alerts", []))
check("H8.7 大年三十可进计算链且（含年份）不触发代年确认",
      r8.get("status") != "error" and "确认" not in a8, a8[:40])
check("H8.8 换算结果 2027-02-05 与显式公历一致",
      r8.get("single", {}).get("entry") == "2027-02-05", str(r8.get("single", {}).get("entry")))

print("== H9 代年方向感知（date_bias）与确认一步到位 ==")
o, t, i = L.parse_lunar("大年三十", near=_date(2026, 10, 22), bias="past")
check("H9.1 大年三十 + past → 过去除夕 2026-02-16（去年大年三十）",
      o.isoformat() == "2026-02-16" and i is True, t)
o, _, _ = L.parse_lunar("大年三十", near=_date(2026, 10, 22), bias="future")
check("H9.2 大年三十 + future → 2027-02-05（计划大年三十出发）", o.isoformat() == "2027-02-05")
o, _, i = L.parse_lunar("大年三十", near=_date(2026, 10, 22))
check("H9.3 大年三十 无 bias → nearest 2027-02-05（兼容 H8.5）",
      o.isoformat() == "2027-02-05" and i is True, str(o))
o, _, _ = L.parse_lunar("腊月初五", near=_date(2026, 10, 22), bias="past")
check("H9.4 腊月初五 + past → 2026-01-23（农历2025年腊月初五）", o.isoformat() == "2026-01-23")
rp = calc({"country": "TH", "today": "2026-10-22", "date_bias": "past", "rules": th_rules,
           "trips": [{"country": "TH", "entry": "大年三十", "exit": None}]})
ap = " ".join(rp.get("alerts", []))
check("H9.5 全链路 past：推定 2026-02-16 且 alert 含公历日期（确认一步到位）",
      rp.get("single", {}).get("entry") == "2026-02-16" and "2026-02-16" in ap and "确认" in ap,
      ap[:70])
rn = calc({"country": "TH", "today": "2026-10-22", "rules": th_rules,
           "trips": [{"country": "TH", "entry": "大年三十", "exit": None}]})
an = " ".join(rn.get("alerts", []))
check("H9.6 无 bias：alert 念出 2027-02-05（公历日期在确认句内）",
      "2027-02-05" in an and "确认" in an, an[:70])

print("== H9b 非法 date_bias 告警（防 typo 静默降级） ==")
rt = calc({"country": "TH", "today": "2026-10-22", "date_bias": "futrue", "rules": th_rules,
           "trips": [{"country": "TH", "entry": "18号", "exit": None}]})
at = " ".join(rt.get("alerts", []))
check("H9.7 typo date_bias 触发告警且不崩",
      rt.get("status") != "error" and "date_bias" in at and "futrue" in at, at[:60])
ra = calc({"country": "TH", "today": "2026-10-22", "date_bias": ["past", "future"],
           "rules": th_rules, "trips": [{"country": "TH", "entry": "18号", "exit": None}]})
check("H9.8 数组 date_bias 触发告警（混合方向无法单值表达）",
      "date_bias" in " ".join(ra.get("alerts", [])), " ".join(ra.get("alerts", []))[:60])
rok = calc({"country": "TH", "today": "2026-10-22", "date_bias": "past", "rules": th_rules,
            "trips": [{"country": "TH", "entry": "18号", "exit": None}]})
check("H9.9 合法值 past 不误报告警",
      "不是" not in " ".join(rok.get("alerts", [])), " ".join(rok.get("alerts", []))[:40])
rmix = calc({"country": "DE", "today": "2026-10-22", "date_bias": "past",
             "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
             "trips": [{"country": "FR", "entry": "2026-05-10", "exit": "2026-05-24"},
                       {"country": "ES", "entry": "2026-09-08", "exit": "2026-09-27"}],
             "as_of": "2026-11-20", "reported_days": 35})
check("H9.10 混合时序单次调用：明确日期不受 date_bias 影响，as_of 锚窗口（5月滑出）",
      rmix.get("window", {}).get("used") == 20 and rmix.get("status") == "planning",
      str(rmix.get("window", {}).get("used")))

print()

print()
print(f"结果: {len(fails)} 失败" + (f" -> {fails}" if fails else ""))
sys.exit(1 if fails else 0)
