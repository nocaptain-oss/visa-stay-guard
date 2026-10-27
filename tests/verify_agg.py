#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A 系列：聚合表（visa-types-aggregated.txt，封装）独立断言。

与 verify_e.py 分文件的原因：聚合表等级不同（未逐条官方直抓），
绝不能用官方表的 E1d/E1e 口径去校验它，否则等于默许二手数据冒充官方源。
本文件只校验「聚合表自身的一致性 + 必须被降级播报」。

用法: python3 tests/verify_agg.py
"""
import json, os, subprocess, sys
from _support import with_supported_profile

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
    return _seal.load(name)

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

# 两级表国家集合必须互斥：同一国家只能用一种核验等级说话，避免「半官方半聚合」
both = set(agg["countries"]) & set(ext["countries"])
check("A5d 两级表国家互斥", not both, str(sorted(both)))

r = subprocess.run(["python3", LOOKUP, "--type", "mm-tourist-voa"], capture_output=True, text=True)
o = json.loads(r.stdout)
check("A5e --type 命中聚合条目并标 tier", o.get("found") and o["type"]["tier"] == "aggregated")

r = subprocess.run(["python3", LOOKUP, "--search", "落地签"], capture_output=True, text=True)
o = json.loads(r.stdout)
check("A5f --search 覆盖聚合表", any(h["tier"] == "aggregated" for h in o.get("matches", [])), str(len(o.get("matches", []))))

# A6 消费链路：聚合规则 → confidence=low / rules.source=aggregated
# 注：缅甸旅游电子签 28 天已与 dataset 重复而去重（dataset 记的就是 28 天电子签），
# 这里改用商务签 70 天作为聚合表消费样本。
biz = next(t for t in agg["countries"]["MM"]["types"] if t["id"] == "mm-business")
voa = next(t for t in agg["countries"]["MM"]["types"] if t["id"] == "mm-tourist-voa")
def run(p):
    p = with_supported_profile(p)
    r = subprocess.run(["python3", CALC], input=json.dumps(p), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[:300]
    return json.loads(r.stdout)

def rule(t):
    return {"stay_single": t["stay_single"], "stay_window": t["stay_window"],
            "stay_window_max": t["stay_window_max"]}

o = run({"country": "MM", "today": "2026-10-21", "rules": rule(biz),
         "trips": [{"country": "MM", "entry": "2026-10-10", "exit": None}],
         "visa_type": biz["type"], "visa_source": "aggregated"})
check("A6a aggregated → confidence=low", o["visa_info"]["confidence"] == "low", o["visa_info"]["confidence"])
check("A6b rules.source=aggregated", o["rules_effective"]["source"] == "aggregated", o["rules_effective"]["source"])
check("A6c MM 商务签 70 天：最晚 12-18", o["single"]["last_allowed_day"] == "2026-12-18", o["single"]["last_allowed_day"])

# A7 入库理由自证：缅甸落地签 30 vs 商务签 70，选错入境方式少 40 天
o = run({"country": "MM", "today": "2026-10-21", "rules": rule(voa),
         "trips": [{"country": "MM", "entry": "2026-10-10", "exit": None}],
         "visa_type": voa["type"], "visa_source": "aggregated"})
check("A7a MM 落地签 30 天：最晚 11-08", o["single"]["last_allowed_day"] == "2026-11-08", o["single"]["last_allowed_day"])
check("A7b 同一入境日两档相差 40 天", biz["stay_single"] - voa["stay_single"] == 40,
      f"{biz['stay_single']}-{voa['stay_single']}")

# A8 每个聚合国家必须至少有一条「真增量」条目（不是 dataset 副本），否则该国不该在表里
ds = load("rules-data")
ds_single = {e["countryCode"]: e.get("staySingle") for e in ds["entries"]}
for code, c in agg["countries"].items():
    real = [t for t in c["types"] if not t.get("duplicates_dataset")]
    check(f"A8a {code} 至少一条真增量", bool(real), f"{len(real)}/{len(c['types'])}")
    for t in c["types"]:
        if t.get("duplicates_dataset"):
            check(f"A8b {t['id']} 副本天数须等于 dataset",
                  t["stay_single"] == ds_single.get(code),
                  f"{t['stay_single']} vs {ds_single.get(code)}")

# A9 dataset 同渠道同天数的条目必须标注，避免「假装增量」
for code, c in agg["countries"].items():
    dv = ds_single.get(code)
    for t in c["types"]:
        if t["stay_single"] == dv and not t.get("duplicates_dataset"):
            check(f"A9 {t['id']} 与 dataset 同天数须显式说明", bool(t.get("notes")),
                  "同天数但未标 duplicates_dataset，至少要在 notes 说明差异")

# A10 单次天数不得超过窗口累计上限 —— 否则会同时输出「本次可待 180 天」和「窗口内累计只允许 90 天」
# 两个互相矛盾的结论，用户按前者安排行程就会超期。埃及五年多次签就是这种情况（单次 180 / dataset 窗口 90）。
for code, c in agg["countries"].items():
    for t in c["types"]:
        if t["stay_window_max"]:
            check(f"A10 {t['id']} 单次≤窗口累计上限",
                  t["stay_single"] <= t["stay_window_max"],
                  f"单次 {t['stay_single']} 天 > 窗口 {t['stay_window']} 天内累计 {t['stay_window_max']} 天")

# A11 声明了日历窗口的国家必须给出年度上限（印度 180 天/日历年），否则日历窗口等于没建模
for code, c in agg["countries"].items():
    for t in c["types"]:
        if t.get("calendar_year_window"):
            check(f"A11 {t['id']} 日历窗口须有年度上限", t["stay_window_max"] > 0,
                  f"calendar_year_window=true 但 stay_window_max={t['stay_window_max']}")

# A12 不得与 dataset 默认口径重复（同国 + 同天数 + 同类型）
# dataset 每国只收一种常见签证；聚合表再列一条一模一样的，用户会看到两条「30 天免签」。
# 「同天数」不等于重复：缅甸电子签 28 ≠ 落地签 30，斐济单次入境签 120 ≠ 免签 30。
import re as _re
WAY = [(r"免签|visa.?free", "free"), (r"落地|visa on arrival|\bvoa\b", "voa"),
       (r"电子签|e-?visa", "evisa"), (r"eTA|电子旅行授权|旅行授权|ETAS", "eta"),
       (r"贴纸|领事", "sticker"), (r"入境许可|entry permit", "permit")]
PURPOSE = [(r"旅游|观光|tourist|度假", "tourist"), (r"商务|business", "business"),
           (r"探亲|family|访友", "family"), (r"工作|劳工|受雇|work", "work"),
           (r"学生|留学|student", "student"), (r"居留|residence|定居", "residence"),
           (r"过境|transit", "transit"), (r"医疗|medical", "medical"),
           (r"朝觐|副朝|umrah|hajj", "hajj"), (r"船员|seaman|海员", "seaman"),
           (r"记者|journalist", "journalist"), (r"文体|文化|体育|culture|sport", "culture")]
# 证件/渠道维度：港澳台的 G/L/T/S/X 签注与「通行证 + 某类签注」是**不同证件类别**，
# 天数相同不代表同一件事（G 签自由行 vs L 签必须跟团，办证入口都不一样）。
CHANNEL = [(r"g\s*签|个人旅游|自由行", "G"), (r"l\s*签|团队旅游|团签", "L"),
           (r"t\s*签", "T"), (r"s\s*签", "S"), (r"x\s*签", "X"),
           (r"通行证", "permit"), (r"入台证", "tw_permit")]
ds_entry = {e["countryCode"]: e for e in ds["entries"]}


def _tags(s, table):
    s = (s or "").lower()
    return {k for pat, k in table if _re.search(pat, s)}


for code, c in agg["countries"].items():
    d = ds_entry.get(code)
    if not d:
        continue
    for t in c["types"]:
        wa, pa = _tags(t["type"], WAY), _tags(t["type"], PURPOSE)
        # dataset 的 visaName 常有「可境内延期+30天」这类描述，方式信息只在 entryKind 里，
        # 只读 visaName 会漏判（卡塔尔「免签 30」正是这样漏掉的）。两处都读。
        _blob = f"{d.get('visaName') or ''} {d.get('entryKind') or ''}"
        wd, pd = _tags(_blob, WAY), _tags(_blob, PURPOSE)
        # 方式族有交集且目的不冲突；或双方都没标方式但目的有交集
        # （如「个人旅游」vs「G类个人旅游签注」）。
        # 注意不能只看方式：那样「商务电子签」会和 dataset「旅游电子签」被判重复。
        # 渠道/证件不同 → 不是同一件事（香港 L 签 vs dataset 的 G 签，同 7 天但是两条渠道）
        ca, cd = _tags(t["type"] + " " + " ".join(t.get("aliases") or []), CHANNEL), _tags(_blob, CHANNEL)
        other_channel = bool(ca and cd and not (ca & cd))
        if pa and pd:
            same_kind = bool(pa & pd) and not other_channel
        else:
            same_kind = bool(wa & wd) and not other_channel
        # 窗口信息有增量就不算重复：dataset 只给 90 天，聚合条目补了「日历年累计 180 天」，
        # 这条多出来的限制正是防超期的关键，删掉反而是信息损失。
        richer = t["stay_window_max"] > 0 and (d.get("stayWindowMax") or 0) == 0
        # 显式豁免：条目自带 keep_reason 说明「为什么多留一条」（必须有非空理由，
        # 否则 A15 会拦下来），用于补 dataset 缺失的办证规则这类情形。
        keep = bool((t.get("keep_reason") or "").strip())
        conflict = t["stay_single"] == d.get("staySingle") and same_kind and not richer and not keep
        check(f"A12 {t['id']} 不与 dataset 重复", not conflict,
              f"dataset「{d.get('visaName')}」{d.get('staySingle')}天 与聚合「{t['type']}」同类型同天数")

# A13 日历窗口的 stay_window 必须等于年度长度 365 —— 否则「日历年」与窗口天数两个字段互相打架
for code, c in agg["countries"].items():
    for t in c["types"]:
        if t.get("calendar_year_window"):
            check(f"A13 {t['id']} 日历窗口长度=365", t["stay_window"] == 365,
                  f"calendar_year_window=true 但 stay_window={t['stay_window']}")

# A14 聚合表不得收录「不适用中国普通护照」的条目（入库已过滤，这里兜底）
# 边界（2026-10-21 复审确认）：本断言只拦「限定**其他护照持有人**」的条目，
# 例如「香港特区护照免签 14 天」——那是给香港居民用的，大陆普通护照不适用。
# **不拦**港澳台本身的入境方式：G/L 旅游签注、T 探亲、S 商务、X 学习是大陆居民用
# 「港澳通行证 / 往来台湾通行证」办的不同证件类别，虽不叫签证，但天数档位差异大
# （7/14/30/90），对出行判断有直接价值，必须收录。
BAD_PP = _re.compile(r"香港特区护照|澳门特区护照|台湾护照|外交护照|公务护照|官员护照")
# 第二道：不适用中国公民的条目，往往不在 type 里说，而是藏在 notes
# （如「CA-4 区域免签…中国公民不适用」「约70国免签…中国不适用」）。
NOT_FOR_CN = _re.compile(r"中国(公民)?不适用|不适用中国|中国公民除外|不含中国|不包含中国")
for code, c in agg["countries"].items():
    for t in c["types"]:
        blob = f"{t['type']} {' '.join(t.get('aliases') or [])} {t.get('notes') or ''}"
        check(f"A14 {t['id']} 适用中国普通护照",
              not BAD_PP.search(blob) and not NOT_FOR_CN.search(blob),
              f"「{t['type']}」限定其他护照类型或明示不适用中国公民")

# A15 keep_reason 必须说明「增量在哪」—— 防止豁免被当成「不想删」的后门
VAGUE = _re.compile(r"^(保留|有用|重要|有价值)[。.]?$")
for code, c in agg["countries"].items():
    for t in c["types"]:
        if "keep_reason" in t:
            kr = (t.get("keep_reason") or "").strip()
            check(f"A15 {t['id']} keep_reason 说明增量", len(kr) >= 12 and not VAGUE.match(kr),
                  f"keep_reason 过短或空泛：{kr!r}")

# A16 与主表口径冲突的条目必须显式标注，且标注里要有主表数字
# 两条数据打架时删掉谁都可能删错（可能聚合对、也可能主表对），
# 正确做法是留着并强制播报双口径，让用户拿两个数字去官方页面核对。
for code, c in agg["countries"].items():
    for t in c["types"]:
        if "conflict_note" not in t:
            continue
        cn = t.get("conflict_note") or ""
        check(f"A16 {t['id']} 冲突标注含主表口径", "主表" in cn and _re.search(r"\d+", cn) is not None,
              f"conflict_note 未给出主表天数：{cn!r}")
        check(f"A16 {t['id']} 冲突条目已标需复核", t.get("needs_official_check") is True,
              "conflict_note 存在但 needs_official_check 不为 true")


# A17 _meta.excluded 里声明「不入表」的国家代码不得出现在表中
# （曾出现 PR/TT/ID 声明排除却仍在表里的情况，说明生成脚本的过滤和声明是两套逻辑）
_EXCL_TEXT = agg.get("_meta", {}).get("excluded", "") or ""
# 同一段里既有「不入表的：LA/ID/PR/TT」也有「马来西亚 MY 已收」——后者是收录声明，先剔掉
_EXCL_TEXT = _re.sub(r"[^。；]*已收[^。；]*", "", _EXCL_TEXT)
_EXCL_CC = set(_re.findall(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])", _EXCL_TEXT))
for _cc in sorted(_EXCL_CC & set(agg["countries"])):
    check(f"A17 {_cc} 已声明排除却仍在表中", False,
          f"_meta.excluded 声明排除，但表内有 {len(agg['countries'][_cc]['types'])} 条")

print(f"\n{len(fails)} 失败"); sys.exit(1 if fails else 0)
