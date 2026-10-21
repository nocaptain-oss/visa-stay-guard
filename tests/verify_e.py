#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E 系列：扩展表（visa-types-extended.json）结构与脚本消费验证

用法: python3 tests/verify_e.py [被测脚本路径]
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "scripts", "stay_calc.py")
EXT = os.path.join(ROOT, "references", "visa-types-extended.json")

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

# E1 扩展表结构合法
ext = json.load(open(EXT))
check("E1a _meta 完备", all(k in ext["_meta"] for k in ("license", "updated_at", "confidence_model")))
th = ext["countries"]["TH"]["types"]
check("E1b TH 两条类型", len(th) == 2, f"got={len(th)}")
ids = [t["id"] for t in th]
check("E1c id 唯一", len(set(ids)) == len(ids))
req = ("type", "stay_single", "stay_window", "stay_window_max", "confidence", "source", "sourceUrl", "sourceTier", "verified_at")
for t in th:
    check(f"E1d {t['id']} 字段完备", all(k in t for k in req), str([k for k in req if k not in t]))
    check(f"E1e {t['id']} 官方源", t["sourceTier"] == "official" and t["sourceUrl"].startswith("https://"), t["sourceUrl"])
    check(f"E1f {t['id']} confidence 合法", t["confidence"] in ("high", "medium"), t["confidence"])

def run(p):
    r = subprocess.run(["python3", SCRIPT], input=json.dumps(p), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)

# E2 脚本 extended 来源 → high + rules.source=extended
tr = th[0]
o = run({"country":"TH","today":"2026-10-21",
         "rules":{"stay_single":tr["stay_single"],"stay_window":tr["stay_window"],"stay_window_max":tr["stay_window_max"]},
         "trips":[{"country":"TH","entry":"2026-10-14","exit":None}],
         "visa_type":tr["type"],"visa_source":"extended"})
check("E2a extended → confidence=high", o["visa_info"]["confidence"]=="high", o["visa_info"]["confidence"])
check("E2b rules.source=extended", o["rules_effective"]["source"]=="extended")
check("E2c TR 单次 60 天：最晚 12-12", o["single"]["last_allowed_day"]=="2026-12-12", o["single"]["last_allowed_day"])

# E3 DTV（confidence=medium）→ 走 web 口径
dtv = th[1]
o = run({"country":"TH","today":"2026-10-21",
         "rules":{"stay_single":dtv["stay_single"],"stay_window":dtv["stay_window"],"stay_window_max":dtv["stay_window_max"]},
         "trips":[],"as_of":"2026-11-20",
         "visa_type":dtv["type"],"visa_source":"web",
         "web_source":{"name":dtv["source"],"url":dtv["sourceUrl"],"date":dtv["verified_at"]}})
check("E3a DTV → confidence=medium", o["visa_info"]["confidence"]=="medium")
check("E3b web_source 指向扩展表页面", o["visa_info"]["web_source"]["url"]==dtv["sourceUrl"])

# E4 extended 不被 visa_days 降级
o = run({"country":"TH","today":"2026-10-21",
         "rules":{"stay_single":60,"stay_window":0,"stay_window_max":0},
         "trips":[],"as_of":"2026-11-20","visa_source":"extended","visa_days":60})
check("E4 extended 保持 source", o["rules_effective"]["source"]=="extended" and o["visa_info"]["confidence"]=="high")

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
