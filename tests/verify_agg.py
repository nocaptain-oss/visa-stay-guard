#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 系列：聚合表（visa-types-aggregated.bin，加密）独立断言。

与 verify_e.py 分文件的原因：聚合表等级不同（未逐条官方直抓），
绝不能用官方表的 E1d/E1e 口径去校验它，否则等于默许二手数据冒充官方源。
本文件只校验「聚合表自身的一致性 + 必须被降级播报」。

用法: python3 tests/verify_agg.py
"""
import json, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import _seal  # noqa: E402

CALC = os.path.join(ROOT, "scripts", "stay_calc.py")
LOOKUP = os.path.join(ROOT, "scripts", "visa_lookup.py")

fails = []
def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {name}  {detail}")
    if not cond: fails.append(name)

def load(name):
    with open(os.path.join(ROOT, "references", name + ".bin"), "rb") as f:
        return json.loads(_seal.unseal_bytes(f.read()).decode("utf-8"))

agg = load("visa-types-aggregated")
ext = load("visa-types-extended")

# A1 表级元信息
m = agg["_meta"]
check("A1a tier=aggregated", m.get("tier") == "aggregated", str(m.get("tier")))
check("A1b _meta 完备", all(k in m for k in ("license", "updated_at", "confidence_model", "note")))
check("A1c 声明需官方复核", "官方" in m.get("note", ""), m.get("note", "")[:40])

# A2 全表条目必须自我声明「低置信 + 待核验」
req = ("id", "type", "stay_single", "stay_window", "stay_window_max",
       "confidence", "source", "sourceUrl", "sourceTier", "verified_at")
ids, pairs = [], set()
for code, c in agg["countries"].items():
    types = c.get("types") or []
    check(f"A2a {code} 有类型且含 countryName", bool(types) and bool(c.get("countryName")), f"types={len(types)}")
    for t in types:
        ids.append(t["id"])
        miss = [k for k in req if k not in t]
        check(f"A2b {t['id']} 字段完备", not miss, str(miss))
        check(f"A2c {t['id']} sourceTier=aggregated", t["sourceTier"] == "aggregated", t["sourceTier"])
        check(f"A2d {t['id']} confidence=low", t["confidence"] == "low", t["confidence"])
        check(f"A2e {t['id']} needs_official_check", t.get("needs_official_check") is True)
        check(f"A2f {t['id']} 有 verified_at", bool(t.get("verified_at")), t.get("verified_at", ""))
        check(f"A2g {t['id']} stay_single>0", t["stay_single"] > 0, str(t["stay_single"]))
        pairs.add((code, t["stay_single"]))
check("A2h id 全表唯一", len(set(ids)) == len(ids), f"{len(ids)}/{len(set(ids))}")

# A3 不得与官方表 id 撞名（撞名会让 --type 查到错的那条）
ext_ids = {t["id"] for c in ext["countries"].values() for t in c.get("types", [])}
check("A3 id 与官方表不冲突", not (set(ids) & ext_ids), str(sorted(set(ids) & ext_ids)))

# A4 与官方表同国时，不得重复同一停留天数（重复即纯冗余，不该入库）
ext_pairs = {(code, t["stay_single"])
             for code, c in ext["countries"].items() for t in c.get("types", [])}
dup = pairs & ext_pairs
check("A4 不重复官方表已有天数", not dup, str(sorted(dup)))

# A5 visa_lookup 必须合并两级并带免责声明
r = subprocess.run(["python3", LOOKUP, "--types", "MM"], capture_output=True, text=True)
o = json.loads(r.stdout)
check("A5a --types MM 命中", o.get("found") is True and len(o["types"]) == 4, str(len(o.get("types", []))))
check("A5b --types 带 tier", all(t.get("tier") == "aggregated" for t in o["types"]))
check("A5c --types 带 warning", "聚合整理" in o.get("warning", ""), o.get("warning", "")[:30])

r = subprocess.run(["python3", LOOKUP, "--types", "MV"], capture_output=True, text=True)
o = json.loads(r.stdout)
tiers = sorted(t["tier"] for t in o["types"])
check("A5d --types MV 两级合并", tiers == ["aggregated", "official", "official"], str(tiers))

r = subprocess.run(["python3", LOOKUP, "--type", "mm-tourist-voa"], capture_output=True, text=True)
o = json.loads(r.stdout)
check("A5e --type 命中聚合条目并标 tier", o.get("found") and o["type"]["tier"] == "aggregated")

r = subprocess.run(["python3", LOOKUP, "--search", "落地签"], capture_output=True, text=True)
o = json.loads(r.stdout)
check("A5f --search 覆盖聚合表", any(h["tier"] == "aggregated" for h in o.get("matches", [])), str(len(o.get("matches", []))))

# A6 消费链路：聚合规则 → confidence=low / rules.source=aggregated
mm = next(t for t in agg["countries"]["MM"]["types"] if t["id"] == "mm-tourist-evisa")
voa = next(t for t in agg["countries"]["MM"]["types"] if t["id"] == "mm-tourist-voa")
def run(p):
    r = subprocess.run(["python3", CALC], input=json.dumps(p), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)

def rule(t):
    return {"stay_single": t["stay_single"], "stay_window": t["stay_window"],
            "stay_window_max": t["stay_window_max"]}

o = run({"country": "MM", "today": "2026-10-21", "rules": rule(mm),
         "trips": [{"country": "MM", "entry": "2026-10-10", "exit": None}],
         "visa_type": mm["type"], "visa_source": "aggregated"})
check("A6a aggregated → confidence=low", o["visa_info"]["confidence"] == "low", o["visa_info"]["confidence"])
check("A6b rules.source=aggregated", o["rules_effective"]["source"] == "aggregated", o["rules_effective"]["source"])
check("A6c MM 电子签 28 天：最晚 11-06", o["single"]["last_allowed_day"] == "2026-11-06", o["single"]["last_allowed_day"])

# A7 入库理由自证：缅甸电子签 28 vs 落地签 30，选错入境方式少 2 天
o = run({"country": "MM", "today": "2026-10-21", "rules": rule(voa),
         "trips": [{"country": "MM", "entry": "2026-10-10", "exit": None}],
         "visa_type": voa["type"], "visa_source": "aggregated"})
check("A7a MM 落地签 30 天：最晚 11-08", o["single"]["last_allowed_day"] == "2026-11-08", o["single"]["last_allowed_day"])
check("A7b 同一入境日两档相差 2 天", voa["stay_single"] - mm["stay_single"] == 2,
      f"{voa['stay_single']}-{mm['stay_single']}")

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
