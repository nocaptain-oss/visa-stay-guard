#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据资产查询入口 —— 模型不再直接读取明文规则文件，一律走这里。

用法：
    python3 scripts/visa_lookup.py --rules KR           # 该国 x/N/y 规则
    python3 scripts/visa_lookup.py --types KR           # 该国多签证类型（扩展表）
    python3 scripts/visa_lookup.py --type kr-c39-double # 单个签证类型
    python3 scripts/visa_lookup.py --countries          # 扩展表覆盖的国家
    python3 scripts/visa_lookup.py --meta               # 两个数据集的版本/授权信息
    python3 scripts/visa_lookup.py --search C-3-9       # 按 id/名称/别名模糊找类型

输出为 JSON，未命中时返回 {"found": false}，不抛异常（便于模型判断下一步）。
"""
import argparse
import json
import os
import sys

import _seal

_rules_cache = None
_ext_cache = None
_agg_cache = None
_cc_cache = None

# 聚合表条目必须随结果一起播报的免责口径（SKILL.md 对应章节依赖此字段）
_WARN = "数据为 AI 聚合整理（confidence=low），未逐条官方直抓；请以签证页与使馆官方口径为准。"


def _countries():
    """别名索引：中文名 / 英文名 / 别名 / 城市名 → ISO 代码。

    读 references/countries.md 的 5 列表格。模型传「日本」「UK」这类非代码值时，
    在这里解析掉——查询入口是唯一通道，容错放在入口比写在 SKILL.md 里可靠。"""
    global _cc_cache
    if _cc_cache is not None:
        return _cc_cache
    idx = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "references", "countries.md")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) != 5:
                    continue  # 排除页内其它小表格
                code = cells[0]
                if len(code) != 2 or not code.isupper() or not code.isascii():
                    continue
                for col in (cells[1], cells[2], cells[4]):
                    for key in [col] + col.split("、"):
                        key = key.strip().lower()
                        if key:
                            idx.setdefault(key, code)
    except OSError:
        pass
    _cc_cache = idx
    return idx


def resolve_cc(raw):
    """国家标识 → ISO 代码。接受 2 位代码 / 中文名 / 英文名 / 别名 / 城市名。"""
    s = (raw or "").strip()
    if not s:
        return s
    idx = _countries()
    low = s.lower()
    if low in idx:
        return idx[low]  # 别名优先：UK → GB、东京 → JP
    if len(s) == 2 and s.isascii() and s.isalpha():
        return s.upper()
    hits = {v for k, v in idx.items() if low in k}  # 只做「输入是别名子串」单向匹配
    if len(hits) == 1:
        return hits.pop()
    return s


def _rules():
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = _seal.load("rules-data")
    return _rules_cache


def _ext():
    global _ext_cache
    if _ext_cache is None:
        _ext_cache = _seal.load("visa-types-extended")
    return _ext_cache


def _agg():
    """二级表：AI 聚合整理数据。文件缺失时视为空表，不影响主流程。"""
    global _agg_cache
    if _agg_cache is None:
        try:
            _agg_cache = _seal.load("visa-types-aggregated")
        except Exception:
            _agg_cache = {"countries": {}, "_meta": {}}
    return _agg_cache


def _merged_types(cc):
    """合并官方表与聚合表同一国家的类型，逐条标注 tier。"""
    hits = []
    for tier, doc in (("official", _ext()), ("aggregated", _agg())):
        node = doc.get("countries", {}).get(cc)
        if not node:
            continue
        for t in node.get("types", []):
            item = dict(t)
            item["tier"] = tier
            hits.append(item)
    return hits


def out(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="visa-stay-guard 数据查询")
    ap.add_argument("--rules", metavar="CC", help="国家代码，取 x/N/y 规则")
    ap.add_argument("--types", metavar="CC", help="国家代码，取扩展表全部签证类型")
    ap.add_argument("--type", metavar="ID", help="签证类型 id")
    ap.add_argument("--search", metavar="KW", help="按 id/类型名/别名模糊匹配")
    ap.add_argument("--countries", action="store_true", help="列出扩展表覆盖的国家")
    ap.add_argument("--meta", action="store_true", help="数据集版本与授权信息")
    a = ap.parse_args()

    if a.meta:
        d, e, g = _rules(), _ext(), _agg()
        out({"rules_data": d.get("_meta"), "extended": e.get("_meta"),
             "aggregated": g.get("_meta")})
        return

    if a.countries:
        # 合并两级，官方优先；同国出现在两级时合并 type_count 并标出 tier
        acc = {}
        for tier, doc in (("official", _ext()), ("aggregated", _agg())):
            for k, v in doc.get("countries", {}).items():
                slot = acc.setdefault(k, {"code": k, "name": v.get("countryName"),
                                          "type_count": 0, "tiers": []})
                slot["type_count"] += len(v.get("types", []))
                slot["tiers"].append(tier)
        out({
            "countries": list(acc.values()),
            "total_types": sum(x["type_count"] for x in acc.values()),
            "tiers": {"official": "官方页面实抓核验", "aggregated": "AI 聚合整理，需官方复核"},
        })
        return

    if a.rules:
        cc = resolve_cc(a.rules)
        for entry in _rules().get("entries", []):
            if entry.get("countryCode") == cc:
                out({"found": True, "countryCode": cc, "entry": entry,
                     **({"resolvedFrom": a.rules} if cc != a.rules else {})})
                return
        out({"found": False, "countryCode": cc, "input": a.rules,
             "hint": "国家名未识别为该表条目。先试 ISO 两位代码（如 JP/GB），"
                     "仍为 false 才是真未收录——不要凭这个 false 下「没有数据」的结论。"})
        return

    if a.types:
        cc = resolve_cc(a.types)
        types = _merged_types(cc)
        if not types:
            out({"found": False, "countryCode": cc, "input": a.types,
                 "hint": "该国不在两级类型表中（类型表只收「同一护照多类型且天数不同」的国家，"
                         "免签国与统一口径国故意不重复收录）——这不代表没有停留规则。"})
            return
        name = next((doc["countries"][cc].get("countryName")
                     for doc in (_ext(), _agg()) if cc in doc.get("countries", {})), None)
        out({"found": True, "countryCode": cc, "countryName": name, "types": types,
             "warning": _WARN})
        return

    if a.type:
        tid = a.type.lower()
        for tier, doc in (("official", _ext()), ("aggregated", _agg())):
            for cc, node in doc.get("countries", {}).items():
                for t in node.get("types", []):
                    if t.get("id") == tid:
                        item = dict(t, tier=tier)
                        out({"found": True, "countryCode": cc, "type": item,
                             **({"warning": _WARN} if tier == "aggregated" else {})})
                        return
        out({"found": False, "id": a.type})
        return

    if a.search:
        kw = a.search.lower()
        hits = []
        for tier, doc in (("official", _ext()), ("aggregated", _agg())):
            for cc, node in doc.get("countries", {}).items():
                for t in node.get("types", []):
                    hay = (t.get("id", "") + t.get("type", "") + " ".join(t.get("aliases", []))).lower()
                    if kw in hay:
                        hits.append({"countryCode": cc, "id": t["id"], "type": t["type"],
                                     "stay_single": t["stay_single"], "confidence": t["confidence"],
                                     "tier": tier})
        # 官方表命中排前面
        hits.sort(key=lambda h: 0 if h["tier"] == "official" else 1)
        out({"found": bool(hits), "keyword": a.search, "matches": hits})
        return

    ap.print_help()
    sys.exit(1)


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    main()
