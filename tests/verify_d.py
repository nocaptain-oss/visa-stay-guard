#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""D 系列：结构化录入 / 校验 / 来源标注验证

用法: python3 tests/verify_d.py [被测脚本路径]
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

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

# D1 结构化录入正确：source 标注 + high + 无疑点
o = run({"country":"DE","today":"2026-10-21",
         "rules":{"stay_single":90,"stay_window":180,"stay_window_max":90},
         "trips":[{"country":"DE","entry":"2026-10-01","exit":None}],
         "visa_source":"user_rules","visa_type":"商务签"})
check("D1a rules.source=user_rules", o["rules_effective"].get("source")=="user_rules", f"got={o['rules_effective'].get('source')}")
check("D1b confidence=high", o["visa_info"]["confidence"]=="high")
check("D1c 无疑点告警", not any("疑点" in a for a in o["alerts"]), f"{o['alerts']}")

# D2 录反 180/90：疑点告警
o = run({"country":"DE","today":"2026-10-21",
         "rules":{"stay_single":180,"stay_window":90,"stay_window_max":90},
         "trips":[{"country":"DE","entry":"2026-10-01","exit":None}],
         "visa_source":"user_rules"})
check("D2 录反疑点告警", any("疑点" in a and "弄反" in a for a in o["alerts"]), f"{o['alerts']}")
check("D2b 录入疑点不得直接播报",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None)

# D3 dataset 同数字不误伤（ES 90/180/90）
o = run({"country":"ES","today":"2026-10-21",
         "rules":{"stay_single":90,"stay_window":180,"stay_window_max":90},
         "trips":[{"country":"ES","entry":"2026-10-01","exit":None}]})
check("D3 dataset 无疑点", not any("疑点" in a for a in o["alerts"]))
check("D3b dataset source=dataset", o["rules_effective"].get("source")=="dataset")

# D4 联网兜底：medium + web_source 透传
o = run({"country":"JP","today":"2026-10-21","as_of":"2027-01-15",
         "rules":{"stay_single":90,"stay_window":0,"stay_window_max":0},
         "trips":[],
         "visa_source":"web",
         "web_source":{"name":"日本国外務省","url":"https://www.mofa.go.jp","date":"2026-10-21"}})
check("D4a web confidence=medium", o["visa_info"]["confidence"]=="medium", f"got={o['visa_info']['confidence']}")
check("D4b web_source 透传", o["visa_info"].get("web_source",{}).get("name")=="日本国外務省")
check("D4c rules.source=web", o["rules_effective"].get("source")=="web")

# D5 user_rules + 签证页单值并存：以签证页为准且 source 保持 user_rules
o = run({"country":"JP","today":"2026-10-21","as_of":"2027-01-15",
         "rules":{"stay_single":30,"stay_window":0,"stay_window_max":0},
         "trips":[],"visa_source":"user_rules","visa_days":90})
check("D5 visa_days 覆盖 single=90", o["rules_effective"]["stay_single"]==90)
check("D5b source 保持 user_rules", o["rules_effective"].get("source")=="user_rules")

# D6 行程倒置：告警 + 不计入
o = run({"country":"FR","today":"2026-10-21",
         "rules":{"stay_single":90,"stay_window":180,"stay_window_max":90},
         "trips":[{"country":"FR","entry":"2026-09-20","exit":"2026-09-01"},
                  {"country":"FR","entry":"2026-10-01","exit":None}]})
check("D6a 倒置告警", any("日期倒置" in a for a in o["alerts"]))
check("D6b 倒置段不计入(used=21)",
      ((o.get("candidate") or {}).get("window") or {}).get("used")==21)
check("D6c 倒置输入不得直接播报",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None)

# D7 缺入境日期告警
o = run({"country":"FR","today":"2026-10-21",
         "rules":{"stay_single":90,"stay_window":180,"stay_window_max":90},
         "trips":[{"country":"FR","exit":"2026-09-01"},
                  {"country":"FR","entry":"2026-10-01","exit":None}]})
check("D7 缺 entry 告警", any("缺入境日期" in a for a in o["alerts"]))
check("D7b 缺 entry 不得直接播报",
      o.get("status") == "needs_confirmation" and o.get("bottom_line") is None)

# D8 单个未来 open trip 是合法规划，不再伪装成当前在途
o = run({"country":"FR","today":"2026-10-21",
         "rules":{"stay_single":90,"stay_window":180,"stay_window_max":90},
         "trips":[{"country":"FR","entry":"2026-12-01","exit":None}]})
check("D8 未来 open trip 进入 planning",
      o.get("status") == "planning" and (o.get("single") or {}).get("entry") == "2026-12-01")

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
