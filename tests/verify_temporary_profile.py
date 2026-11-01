#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P 系列：会话级临时签证模型、照片字段回填与解释式追问。"""

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
        "country": "JP",
        "today": "2026-06-14",
        "passport_nationality": "CN",
        "passport_type": "ordinary",
        "residence_status": "visitor",
        "trips": [{"country": "JP", "entry": "2026-06-12", "exit": None}],
        **payload,
    })


BASE_RULES = {
    "stay_single": 90,
    "stay_window": 0,
    "stay_window_max": 0,
    "calendar_year_window": False,
}
BASE_PROFILE = {
    "origin": "conversation",
    "user_confirmed": True,
    "visa_type": "日本学生居留",
    "visa_class": "student",
    "entry_basis": "visa",
    "rules": BASE_RULES,
    "visa_valid_until": "2026-12-31",
    "visa_expiry_semantics": "leave_by",
    "conditions": [],
    "source": {"kind": "user_statement", "name": "用户对话逐项确认"},
}


# P1：确认后的学生类临时模型可进入通用日期引擎
o = run({"temporary_profile": BASE_PROFILE})
check("P1a 临时学生模型可计算", o["status"] == "normal", o["status"])
check("P1b 结论来自临时规则", "2026-09-09" in (o["bottom_line"] or ""), o["bottom_line"])
check("P1c rules.source=temporary",
      o["rules_effective"]["source"] == "temporary", str(o["rules_effective"]))
check("P1d visa_source=temporary",
      o["visa_info"]["visa_source"] == "temporary", str(o["visa_info"]))
check("P1e 临时模型置信度=medium",
      o["visa_info"]["confidence"] == "medium", o["visa_info"]["confidence"])
check("P1f 临时模型仅会话保存",
      o["temporary_profile"]["persistence"] == "session_only"
      and o["temporary_profile"]["used"] is True, str(o["temporary_profile"]))
check("P1g 文字模式默认不上传图片",
      o["temporary_profile"]["privacy"]["input_mode"] == "manual_text"
      and o["temporary_profile"]["privacy"]["cloud_transfer"] == "none",
      str(o["temporary_profile"]["privacy"]))


# P2：照片提取只生成草案，用户确认前不出数字
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "origin": "photo",
    "user_confirmed": False,
    "source": {"kind": "photo", "name": "签证页 OCR 草稿"},
}})
check("P2a 未确认照片草案待确认", o["status"] == "needs_confirmation", o["status"])
check("P2b 未确认照片草案无结论", o["bottom_line"] is None, str(o["bottom_line"]))
check("P2c used=false", o["temporary_profile"]["used"] is False, str(o["temporary_profile"]))
check("P2d 追问草案核对",
      any("临时签证规则草案" in q for q in o["questions"]), str(o["questions"]))
item = next((x for x in o["clarifications"]
             if x.get("field") == "temporary_profile.user_confirmed"), None)
check("P2e 解释包含 why/where",
      bool(item and item.get("why") and item.get("where")), str(item))
check("P2f 图片隐私未声明同样阻断",
      any(x.get("field") == "temporary_profile.privacy" for x in o["clarifications"]),
      str(o["clarifications"]))


# P3：确认后的照片草案可计算，但来源仍如实标为 photo
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "origin": "photo",
    "source": {"kind": "photo", "name": "签证页照片"},
    "privacy": {
        "input_mode": "session_attachment",
        "cloud_transfer": "model_input",
        "user_consented": True,
        "raw_image_retained": False,
    },
}})
check("P3a 已确认照片草案可计算", o["status"] == "normal", o["status"])
check("P3b origin=photo",
      o["temporary_profile"]["origin"] == "photo", str(o["temporary_profile"]))
check("P3c source.kind=photo",
      o["temporary_profile"]["source"]["kind"] == "photo", str(o["temporary_profile"]))
check("P3d 不回显图片内容",
      all("image" not in k.lower() and "base64" not in k.lower()
          for k in o["temporary_profile"]), str(o["temporary_profile"]))
check("P3e 云端模型输入经明确同意",
      o["temporary_profile"]["privacy"]["cloud_transfer"] == "model_input"
      and o["temporary_profile"]["privacy"]["user_consented"] is True,
      str(o["temporary_profile"]["privacy"]))


# P4：算法无法表达的附加条件必须阻断整次计算
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "conditions": ["每周只能工作 20 小时"],
}})
check("P4a 未建模条件→unsupported", o["status"] == "unsupported", o["status"])
check("P4b 不得给天数结论", o["bottom_line"] is None, str(o["bottom_line"]))
check("P4c 如实列出未建模条件",
      any("每周只能工作 20 小时" in a for a in o["alerts"]), str(o["alerts"]))
check("P4d 未建模条件标记 used=false",
      o["temporary_profile"]["used"] is False, str(o["temporary_profile"]))


# P5：缺规则不能只靠类别算出天数
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "rules": {},
}})
check("P5a 缺临时规则→needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("P5b 追问停留上限",
      any("停留上限" in q for q in o["questions"]), str(o["questions"]))
check("P5c 结构化字段=temporary_profile.rules",
      any(x.get("field") == "temporary_profile.rules" for x in o["clarifications"]),
      str(o["clarifications"]))
check("P5d 缺规则标记 used=false",
      o["temporary_profile"]["used"] is False, str(o["temporary_profile"]))


# P6：临时模型与顶层实例冲突时不得任意选一份
try:
    run({
        "visa_class": "short_stay",
        "temporary_profile": BASE_PROFILE,
    })
    conflict_error = None
except stay_calc.InputError as exc:
    conflict_error = str(exc)
check("P6 顶层与临时模型冲突必须报错",
      bool(conflict_error and "冲突" in conflict_error), str(conflict_error))


# P7：不能只传 visa_source=temporary 绕过草案确认
try:
    run({"visa_source": "temporary"})
    source_error = None
except stay_calc.InputError as exc:
    source_error = str(exc)
check("P7 temporary 来源必须附带 profile",
      bool(source_error and "temporary_profile" in source_error), str(source_error))


# P8：临时规则沿用结构化录入的防呆校验
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "rules": {
        "stay_single": 180,
        "stay_window": 90,
        "stay_window_max": 90,
        "calendar_year_window": False,
    },
}})
check("P8a 规则录反→needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("P8b 追问含录入疑点",
      any("录入疑点" in q for q in o["questions"]), str(o["questions"]))
check("P8c confirmation 也有 why/where",
      any(x.get("field") == "confirmation" and x.get("why") and x.get("where")
          for x in o["clarifications"]), str(o["clarifications"]))


# P9：所有用户可见追问都必须有结构化解释
o = run({
    "rules": BASE_RULES,
    "temporary_profile": {
        **BASE_PROFILE,
        "visa_class": None,
        "entry_basis": None,
        "user_confirmed": False,
    },
})
check("P9a 多字段缺失仍需确认", o["status"] == "needs_confirmation", o["status"])
check("P9b questions 与 clarifications 完整对应",
      all(any(x.get("question") == q for x in o["clarifications"])
          for q in o["questions"]),
      f"questions={o['questions']} clarifications={o['clarifications']}")


# P10：会话图片可能会走云端，但没有用户同意时不能计算
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "origin": "photo",
    "source": {"kind": "photo", "name": "会话附件"},
    "privacy": {
        "input_mode": "session_attachment",
        "cloud_transfer": "model_input",
        "user_consented": False,
        "raw_image_retained": False,
    },
}})
check("P10a 云端图片无同意→needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("P10b 追问单独同意",
      any(x.get("field") == "temporary_profile.privacy.user_consented"
          for x in o["clarifications"]), str(o["clarifications"]))
check("P10c used=false", o["temporary_profile"]["used"] is False,
      str(o["temporary_profile"]))


# P11：Skill 不直接调用设备摄像头
try:
    run({"temporary_profile": {
        **BASE_PROFILE,
        "origin": "photo",
        "privacy": {
            "input_mode": "camera",
            "cloud_transfer": "none",
            "user_consented": True,
            "raw_image_retained": False,
        },
    }})
    camera_error = None
except stay_calc.InputError as exc:
    camera_error = str(exc)
check("P11 camera 输入方式禁止",
      bool(camera_error and "摄像头" in camera_error), str(camera_error))


# P12：原图仍保留时必须先清理
o = run({"temporary_profile": {
    **BASE_PROFILE,
    "origin": "photo",
    "privacy": {
        "input_mode": "local_path",
        "cloud_transfer": "none",
        "user_consented": True,
        "raw_image_retained": True,
    },
}})
check("P12a 原图保留→needs_confirmation",
      o["status"] == "needs_confirmation", o["status"])
check("P12b 追问删除原图缓存",
      any(x.get("field") == "temporary_profile.privacy.raw_image_retained"
          for x in o["clarifications"]), str(o["clarifications"]))


# P13：粘贴 OCR / 本地 OCR 默认不要求图片离开设备
for field, mode, origin in (
    ("pasted_ocr", "pasted_ocr", "conversation"),
    ("local_ocr", "local_ocr", "photo"),
):
    o = run({"temporary_profile": {
        **BASE_PROFILE,
        "origin": origin,
        "source": {"kind": "user_statement" if mode == "pasted_ocr" else "photo"},
        "privacy": {
            "input_mode": mode,
            "cloud_transfer": "none",
            "user_consented": True,
            "raw_image_retained": False,
        },
    }})
    check(f"P13 {field} 本机模式可计算", o["status"] == "normal", o["status"])

print(f"\n{len(fails)} 失败")
sys.exit(1 if fails else 0)
