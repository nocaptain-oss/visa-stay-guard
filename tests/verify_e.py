#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E 系列：扩展表（visa-types-extended.bin，加密）结构与脚本消费验证

覆盖：全表结构合法性（遍历所有国家/类型）+ JP/AU/TH 的脚本消费链路。

用法: python3 tests/verify_e.py [被测脚本路径]
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "scripts", "stay_calc.py")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _seal  # noqa: E402

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

# E1 全表结构合法（遍历）
with open(os.path.join(ROOT, "references", "visa-types-extended.bin"), "rb") as _f:
    ext = json.loads(_seal.unseal_bytes(_f.read()).decode("utf-8"))
check("E1a _meta 完备", all(k in ext["_meta"] for k in ("license", "updated_at", "confidence_model")))
req = ("id", "type", "stay_single", "stay_window", "stay_window_max",
       "confidence", "source", "sourceUrl", "sourceTier", "verified_at")
all_ids = []
for code, c in ext["countries"].items():
    types = c.get("types") or []
    check(f"E1b {code} 有类型且含 countryName", bool(types) and bool(c.get("countryName")), f"types={len(types)}")
    for t in types:
        all_ids.append(t["id"])
        miss = [k for k in req if k not in t]
        check(f"E1c {t['id']} 字段完备", not miss, str(miss))
        check(f"E1d {t['id']} 官方源", t["sourceTier"] == "official" and t["sourceUrl"].startswith("https://"), t["sourceUrl"])
        check(f"E1e {t['id']} confidence 合法", t["confidence"] in ("high", "medium"), t["confidence"])
        check(f"E1f {t['id']} 数字非负", t["stay_single"] >= 0 and t["stay_window"] >= 0, f"{t['stay_single']}/{t['stay_window']}")
        check(f"E1g {t['id']} sourceUrl 非首页", t["sourceUrl"].rstrip("/").count("/") >= 3, t["sourceUrl"])
check("E1h id 全表唯一", len(set(all_ids)) == len(all_ids), f"{len(all_ids)} 条 / {len(set(all_ids))} 唯一")

def rule(t):
    return {"stay_single": t["stay_single"], "stay_window": t["stay_window"],
            "stay_window_max": t["stay_window_max"]}

def run(p):
    r = subprocess.run(["python3", SCRIPT], input=json.dumps(p), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)

# E2 泰国 TR：extended → high
tr = next(t for t in ext["countries"]["TH"]["types"] if t["id"] == "th-tr")
o = run({"country": "TH", "today": "2026-10-21", "rules": rule(tr),
         "trips": [{"country": "TH", "entry": "2026-10-14", "exit": None}],
         "visa_type": tr["type"], "visa_source": "extended"})
check("E2a extended → confidence=high", o["visa_info"]["confidence"] == "high", o["visa_info"]["confidence"])
check("E2b rules.source=extended", o["rules_effective"]["source"] == "extended")
check("E2c TR 单次 60 天：最晚 12-12", o["single"]["last_allowed_day"] == "2026-12-12", o["single"]["last_allowed_day"])

# E3 泰国 DTV（medium）→ 走 web 口径
dtv = next(t for t in ext["countries"]["TH"]["types"] if t["id"] == "th-dtv")
o = run({"country": "TH", "today": "2026-10-21", "rules": rule(dtv),
         "trips": [], "as_of": "2026-11-20",
         "visa_type": dtv["type"], "visa_source": "web",
         "web_source": {"name": dtv["source"], "url": dtv["sourceUrl"], "date": dtv["verified_at"]}})
check("E3a DTV → confidence=medium", o["visa_info"]["confidence"] == "medium")
check("E3b web_source 指向扩展表页面", o["visa_info"]["web_source"]["url"] == dtv["sourceUrl"])

# E4 extended 不被 visa_days 降级
o = run({"country": "TH", "today": "2026-10-21",
         "rules": {"stay_single": 60, "stay_window": 0, "stay_window_max": 0},
         "trips": [], "as_of": "2026-11-20", "visa_source": "extended", "visa_days": 60})
check("E4 extended 保持 source", o["rules_effective"]["source"] == "extended" and o["visa_info"]["confidence"] == "high")

# E5 日本三年多次：30 天/次
jp3 = next(t for t in ext["countries"]["JP"]["types"] if t["id"] == "jp-tourist-multi-3y")
o = run({"country": "JP", "today": "2026-10-21", "rules": rule(jp3),
         "trips": [{"country": "JP", "entry": "2026-10-15", "exit": None}],
         "visa_type": jp3["type"], "visa_source": "extended"})
check("E5a JP 三年多次 → 30 天", o["single"]["effective_days"] == 30, f"got={o['single']['effective_days']}")
check("E5b JP 三年多次 → high", o["visa_info"]["confidence"] == "high")
check("E5c JP 最晚离境 11-13", o["single"]["last_allowed_day"] == "2026-11-13", o["single"]["last_allowed_day"])

# E6 日本五年多次：90 天/次
jp5 = next(t for t in ext["countries"]["JP"]["types"] if t["id"] == "jp-tourist-multi-5y")
o = run({"country": "JP", "today": "2026-10-21", "rules": rule(jp5),
         "trips": [{"country": "JP", "entry": "2026-10-15", "exit": None}],
         "visa_type": jp5["type"], "visa_source": "extended"})
check("E6a JP 五年多次 → 90 天", o["single"]["effective_days"] == 90, f"got={o['single']['effective_days']}")

# E7 澳洲常旅客：90 天/次 + 多次
auf = next(t for t in ext["countries"]["AU"]["types"] if t["id"] == "au-600-frequent")
o = run({"country": "AU", "today": "2026-10-21", "rules": rule(auf),
         "trips": [{"country": "AU", "entry": "2026-10-01", "exit": None}],
         "visa_type": auf["type"], "visa_source": "extended"})
check("E7a AU 常旅客 → 90 天", o["single"]["effective_days"] == 90, f"got={o['single']['effective_days']}")
check("E7b AU 最晚离境 12-29", o["single"]["last_allowed_day"] == "2026-12-29", o["single"]["last_allowed_day"])

# E8 菲律宾持美签免签：取保守 7 天（页面写可延至 21 天，高报会害用户超期）
php = next(t for t in ext["countries"]["PH"]["types"] if t["id"] == "ph-7day-ajacssuk")
o = run({"country": "PH", "today": "2026-10-21", "rules": rule(php),
         "trips": [{"country": "PH", "entry": "2026-10-15", "exit": None}],
         "visa_type": php["type"], "visa_source": "extended"})
check("E8a PH 持美签 → 7 天", o["single"]["effective_days"] == 7, f"got={o['single']['effective_days']}")
check("E8b PH 最晚离境 10-21（含入境当日）", o["single"]["last_allowed_day"] == "2026-10-21", o["single"]["last_allowed_day"])

# E9 韩国 C-3-9：两次签 30 天 vs 单次签 90 天（反直觉，最容易办错档）
kr1 = next(t for t in ext["countries"]["KR"]["types"] if t["id"] == "kr-c39-single")
kr2 = next(t for t in ext["countries"]["KR"]["types"] if t["id"] == "kr-c39-double")
o = run({"country": "KR", "today": "2026-10-21", "rules": rule(kr1),
         "trips": [{"country": "KR", "entry": "2026-10-01", "exit": None}],
         "visa_type": kr1["type"], "visa_source": "extended"})
check("E9a KR 单次签 → 90 天", o["single"]["effective_days"] == 90, f"got={o['single']['effective_days']}")
o = run({"country": "KR", "today": "2026-10-21", "rules": rule(kr2),
         "trips": [{"country": "KR", "entry": "2026-10-01", "exit": None}],
         "visa_type": kr2["type"], "visa_source": "extended"})
check("E9b KR 两次签 → 30 天（不是 90）", o["single"]["effective_days"] == 30, f"got={o['single']['effective_days']}")
check("E9c KR 两次签最晚离境 10-30", o["single"]["last_allowed_day"] == "2026-10-30", o["single"]["last_allowed_day"])

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
