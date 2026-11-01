#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q 系列：解释式追问与计算依据。"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import stay_calc  # noqa: E402

fails = []


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'} {name}  {detail}")
    if not condition:
        fails.append(name)


def run(payload):
    return stay_calc.run({
        "passport_nationality": "CN",
        "passport_type": "ordinary",
        "residence_status": "visitor",
        **payload,
    })


ES_RULES = {"stay_single": 90, "stay_window": 180, "stay_window_max": 90}


# Q1：缺字段时输出可复述的追问，不泄露候选数字
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": ES_RULES,
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
question = (o["clarifications"] or [{}])[0]
check("Q1a 缺身份进入澄清阶段",
      o["status"] == "needs_confirmation"
      and o["explanation"]["phase"] == "clarification", o["status"])
check("Q1b 追问含 category/affects/recap",
      all(question.get(k) for k in ("category", "affects", "recap")), str(question))
check("Q1c next_step 使用可复述摘要",
      o["explanation"]["next_step"] == question["recap"],
      str(o["explanation"]))
check("Q1d 澄清阶段不输出计算依据",
      o["explanation"]["calculation_basis"] is None and o["single"] is None,
      str(o["explanation"]))


# Q2：签证有效期先于停留边界时，解释由谁封顶
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": ES_RULES,
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "visa_valid_until": "2026-08-01",
    "visa_expiry_semantics": "leave_by",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
basis = o["explanation"]["calculation_basis"]
check("Q2a 正常计算进入 calculation 阶段",
      o["explanation"]["phase"] == "calculation", str(o["explanation"]))
check("Q2b 列出停留规则与签证有效期",
      {x["name"] for x in basis["constraints"]} >= {"stay_rules", "visa_validity"},
      str(basis["constraints"]))
check("Q2c 真实边界由签证有效期封顶",
      basis["binding_constraint"] == "visa_expiry"
      and basis["actual_last_allowed_day"] == "2026-08-01"
      and "有效期" in basis["why"], str(basis))
check("Q2d 保留纯停留规则边界",
      next(x for x in basis["constraints"] if x["name"] == "stay_rules")["result"]
      == "2026-09-09", str(basis["constraints"]))


# Q3：美签由 I-94 截止日封顶
o = run({
    "country": "US",
    "today": "2026-06-14",
    "rules": {"stay_single": 180, "stay_window": 0, "stay_window_max": 0},
    "visa_class": "short_stay",
    "entry_basis": "visa",
    "visa_valid_until": "2026-06-30",
    "visa_expiry_semantics": "entry_by",
    "admission_until": "2026-06-13",
    "trips": [{"country": "US", "entry": "2026-06-12", "exit": None}],
})
basis = o["explanation"]["calculation_basis"]
check("Q3a binding=admission_limit",
      basis["binding_constraint"] == "admission_limit", str(basis))
check("Q3b why 说明 I-94 封顶",
      "I-94" in basis["why"] and basis["actual_last_allowed_day"] == "2026-06-13",
      str(basis))


# Q4：免签路径明确说明不需要签证有效期封顶
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": ES_RULES,
    "visa_class": "short_stay",
    "entry_basis": "visa_free",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
basis = o["explanation"]["calculation_basis"]
check("Q4a 免签 binding=not_required",
      basis["binding_constraint"] == "not_required", str(basis))
check("Q4b 解释免签为何不用 UNTIL",
      "免签" in basis["why"], str(basis))


# Q5：规则无法形式化时也解释停止原因
o = run({
    "country": "ES",
    "today": "2026-06-14",
    "rules": ES_RULES,
    "visa_class": "student",
    "entry_basis": "visa",
    "trips": [{"country": "ES", "entry": "2026-06-12", "exit": None}],
})
check("Q5a unsupported 阶段",
      o["status"] == "unsupported"
      and o["explanation"]["phase"] == "unsupported", str(o["explanation"]))
check("Q5b 说明规则无法形式化",
      "无法形式化" in o["explanation"]["calculation_basis"]["why"],
      str(o["explanation"]))
check("Q5c unsupported 的 next_step 不再要求补日期",
      "无法完整形式化" in o["explanation"]["next_step"], str(o["explanation"]))


# Q6：隐私追问也有分类、影响和下一步
o = run({
    "country": "JP",
    "today": "2026-06-14",
    "trips": [{"country": "JP", "entry": "2026-06-12", "exit": None}],
    "temporary_profile": {
        "origin": "photo",
        "user_confirmed": True,
        "visa_class": "short_stay",
        "entry_basis": "visa",
        "rules": {"stay_single": 15, "stay_window": 0,
                  "stay_window_max": 0, "calendar_year_window": False},
        "visa_valid_until": "2026-12-31",
        "visa_expiry_semantics": "leave_by",
        "conditions": [],
        "source": {"kind": "photo"},
    },
})
privacy = next(
    x for x in o["clarifications"]
    if x["field"] == "temporary_profile.privacy"
)
check("Q6a 隐私问题分类为 privacy",
      privacy["category"] == "privacy", str(privacy))
check("Q6b 隐私问题说明上传与残留风险",
      "上传" in privacy["affects"] or "图片" in privacy["affects"], str(privacy))

print(f"\n{len(fails)} 失败")
sys.exit(1 if fails else 0)
