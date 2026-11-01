#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V 系列：签证有效期语义与最晚离境日冲突。

覆盖：
- leave_by：到期日截断停留天数 / 90-180 结果
- entry_by：到期日只限制入境，不倒推离境
- 结束日期与语义缺一项时必须追问
- 未提供有效期时输出“未校验”，不静默装作已校验
- 到期当天合法、次日超期

用法: python3 tests/verify_validity.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import stay_calc  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond:
        fails.append(name)


def run(payload):
    return stay_calc.run({
        "passport_nationality": "CN",
        "passport_type": "ordinary",
        "residence_status": "visitor",
        **payload,
    })


BASE = {
    "country": "ES",
    "today": "2026-06-14",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "visa_type": "申根C类多次旅游签",
    "visa_source": "user",
    "visa_days": 90,
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
}

# V1：有效期先到，必须截断停留算法算出的 9/9
o = run({**BASE, "visa_valid_until": "2026-06-12", "visa_expiry_semantics": "leave_by"})
check("V1a status=overdue", o["status"] == "overdue", o["status"])
check("V1b 真正最晚离境=6/12", o["single"]["last_allowed_day"] == "2026-06-12",
      o["single"]["last_allowed_day"])
check("V1c 停留规则原值保留 9/9",
      o["single"]["stay_limit_last_allowed_day"] == "2026-09-09",
      o["single"]["stay_limit_last_allowed_day"])
check("V1d binding=visa_expiry",
      o["visa_validity"]["binding_constraint"] == "visa_expiry",
      str(o["visa_validity"]))

# V2：到期日当天仍合法，次日才超期
o = run({
    **BASE,
    "today": "2026-06-12",
    "visa_valid_until": "2026-06-12",
    "visa_expiry_semantics": "leave_by",
})
check("V2a 到期日当天非 overdue", o["status"] != "overdue", o["status"])
check("V2b 到期日当天剩余 0 天", o["single"]["remaining_days"] == 0,
      str(o["single"]["remaining_days"]))

# V3：美签类只限制最晚入境，不限制已合法入境后的离境
o = run({
    **BASE,
    "visa_valid_until": "2026-06-12",
    "visa_expiry_semantics": "entry_by",
    "admission_until": "2026-09-09",
})
check("V3a entry_by 不倒推离境", o["single"]["last_allowed_day"] == "2026-09-09",
      o["single"]["last_allowed_day"])
check("V3b entry_by 不因签证到期而 overdue", o["status"] == "normal", o["status"])
check("V3c binding=stay_rules",
      o["visa_validity"]["binding_constraint"] == "stay_rules",
      str(o["visa_validity"]))

# V4：未来入境晚于有效期，直接阻断
o = run({
    "country": "JP",
    "today": "2026-10-21",
    "as_of": "2026-10-25",
    "rules": {"stay_single": 15, "stay_window": 0, "stay_window_max": 0},
    "visa_type": "单次旅游签",
    "visa_source": "user",
    "visa_days": 15,
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "visa_valid_until": "2026-10-24",
    "visa_expiry_semantics": "entry_by",
    "trips": [],
})
check("V4a 过期签证不能计划入境", o["status"] == "blocked", o["status"])
check("V4b blocked 不给 bottom_line", o["bottom_line"] is None, str(o["bottom_line"]))

# V5/V6：只给其中一项，必须追问另一项
o = run({**BASE, "visa_valid_until": "2026-06-12"})
check("V5 缺语义 → needs_confirmation", o["status"] == "needs_confirmation", o["status"])
check("V5 追问申根 C/美国 B1/B2",
      any("申根 C" in q and "美国 B1/B2" in q for q in o["questions"]), str(o["questions"]))

o = run({**BASE, "visa_expiry_semantics": "leave_by"})
check("V6 缺结束日 → needs_confirmation", o["status"] == "needs_confirmation", o["status"])
check("V6 追问有效期末日",
      any("有效期末日" in q for q in o["questions"]), str(o["questions"]))

# V7：完全没有有效期字段时必须要求补齐
o = run(BASE)
check("V7a 缺字段触发确认", o["status"] == "needs_confirmation", o["status"])
check("V7b checked=false", o["visa_validity"]["checked"] is False, str(o["visa_validity"]))
check("V7b2 binding=unchecked",
      o["visa_validity"]["binding_constraint"] == "unchecked",
      str(o["visa_validity"]))
check("V7c 明确提示未校验有效期",
      any("未提供签证有效期" in a for a in o["alerts"]), str(o["alerts"]))
check("V7d 追问日期与语义",
      any("有效期最后一天" in q and "最晚离境" in q and "最晚入境" in q for q in o["questions"]),
      str(o["questions"]))

# V8：中文别名与英文 canonical 等价
o = run({**BASE, "visa_valid_until": "2026-06-12", "visa_expiry_semantics": "申根类"})
check("V8 中文语义别名", o["visa_validity"]["expiry_semantics"] == "leave_by",
      str(o["visa_validity"]))

# V9：长期/学生/工作/居留类不得进入短期访客算法
o = run({
    **BASE,
    "visa_class": "long_stay",
    "visa_type": "申根D类长期签证",
    "visa_valid_until": "2027-12-31",
    "visa_expiry_semantics": "leave_by",
})
check("V9a 长期签证不进入 C 类计算", o["status"] == "unsupported", o["status"])
check("V9b 长期签证不给底部结论", o["bottom_line"] is None, str(o["bottom_line"]))

# V10：签证名称出现 D/长期，但用户误选短期访客，必须拦截
o = run({
    **BASE,
    "visa_class": "short_stay",
    "visa_type": "申根D类长期签证",
    "visa_valid_until": "2027-12-31",
    "visa_expiry_semantics": "leave_by",
})
check("V10 D 类文本与 short_stay 冲突 → needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("V10 提示核对 TYPE",
      any("D/长期" in q and "TYPE" in q for q in o["questions"]), str(o["questions"]))

# V10b：带空格的 Schengen D visa 同样不能逃过 C/D 冲突检查
o = run({
    **BASE,
    "visa_class": "short_stay",
    "visa_type": "Schengen D visa",
    "visa_valid_until": "2027-12-31",
    "visa_expiry_semantics": "leave_by",
})
check("V10b 空格 D visa 仍触发冲突确认",
      o["status"] == "needs_confirmation", o["status"])

# V11：美签类当前在途必须有 I-94/入境许可日期
o = run({
    "country": "US",
    "today": "2026-06-14",
    "rules": {"stay_single": 180, "stay_window": 0, "stay_window_max": 0},
    "visa_type": "B1/B2",
    "visa_source": "user",
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "visa_valid_until": "2026-06-30",
    "visa_expiry_semantics": "entry_by",
    "trips": [{"country": "US", "entry": "2026-06-12", "exit": None}],
})
check("V11a 缺 I-94 → needs_confirmation", o["status"] == "needs_confirmation", o["status"])
check("V11b 追问 Admit Until Date",
      any("Admit Until Date" in q for q in o["questions"]), str(o["questions"]))

# V12：美签类由入境许可截止日决定离境，而不是签证到期日
o = run({
    "country": "US",
    "today": "2026-06-14",
    "rules": {"stay_single": 180, "stay_window": 0, "stay_window_max": 0},
    "visa_type": "B1/B2",
    "visa_source": "user",
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "visa_valid_until": "2026-06-30",
    "visa_expiry_semantics": "entry_by",
    "admission_until": "2026-06-13",
    "trips": [{"country": "US", "entry": "2026-06-12", "exit": None}],
})
check("V12a 按 I-94 截止日 overdue", o["status"] == "overdue", o["status"])
check("V12b 真正边界=I-94 截止日",
      o["single"]["last_allowed_day"] == "2026-06-13",
      str(o["single"]))
check("V12c binding=admission_limit",
      o["visa_validity"]["binding_constraint"] == "admission_limit",
      str(o["visa_validity"]))

# V13：完全不给签证身份时，不得借用国家默认值直接计算
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
check("V13a 缺签证身份 → needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("V13b 缺签证身份不给结论", o["bottom_line"] is None, str(o["bottom_line"]))
check("V13c 追问 TYPE/CATEGORY 且解释 C/D 差异",
      any("TYPE/CATEGORY" in q and "C 和 D" in q for q in o["questions"]),
      str(o["questions"]))

# V14：短期访客还须确认凭免签、签证还是入境许可停留
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "visa_class": "short_stay",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
check("V14a 缺入境依据 → needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("V14b 追问免签/签证/入境许可",
      any("免签" in q and "签证" in q and "入境许可" in q for q in o["questions"]),
      str(o["questions"]))

# V15：明确免签时不强求签证页 UNTIL
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": {"stay_single": 90, "stay_window": 180, "stay_window_max": 90},
    "visa_class": "short_stay",
    "entry_basis": "visa_free",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
check("V15a 免签可直接计算", o["status"] == "normal", o["status"])
check("V15b visa validity=not_required",
      o["visa_validity"]["binding_constraint"] == "not_required",
      str(o["visa_validity"]))
check("V15c 不再追问签证日期", not o["questions"], str(o["questions"]))

# V16：未来计划只能给上限推演，不能冒充入境后的获准停留
o = run({
    **BASE,
    "today": "2026-06-14",
    "as_of": "2026-07-01",
    "visa_valid_until": "2026-12-31",
    "visa_expiry_semantics": "entry_by",
    "trips": [],
})
check("V16a 未来美签类进入 planning", o["status"] == "planning", o["status"])
check("V16b 结论标明入境后以 I-94 为准",
      "I-94/入境许可" in (o["bottom_line"] or ""), str(o["bottom_line"]))
check("V16c 同时给出解释性告警",
      any("未来尚未入境" in a and "最长停留上限" in a for a in o["alerts"]),
      str(o["alerts"]))

print(f"\n{len(fails)} 失败")
sys.exit(1 if fails else 0)
