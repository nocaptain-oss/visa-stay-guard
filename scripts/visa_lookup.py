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
import sys

import _seal

_rules_cache = None
_ext_cache = None


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
        d, e = _rules(), _ext()
        out({"rules_data": d.get("_meta"), "extended": e.get("_meta")})
        return

    if a.countries:
        e = _ext()
        out({
            "countries": [
                {"code": k, "name": v.get("countryName"), "type_count": len(v.get("types", []))}
                for k, v in e["countries"].items()
            ],
            "total_types": sum(len(v.get("types", [])) for v in e["countries"].values()),
        })
        return

    if a.rules:
        cc = a.rules.upper()
        for entry in _rules().get("entries", []):
            if entry.get("countryCode") == cc:
                out({"found": True, "entry": entry})
                return
        out({"found": False, "countryCode": cc})
        return

    if a.types:
        cc = a.types.upper()
        node = _ext()["countries"].get(cc)
        if not node:
            out({"found": False, "countryCode": cc})
            return
        out({"found": True, "countryCode": cc, "countryName": node.get("countryName"),
             "types": node.get("types", [])})
        return

    if a.type:
        tid = a.type.lower()
        for cc, node in _ext()["countries"].items():
            for t in node.get("types", []):
                if t.get("id") == tid:
                    out({"found": True, "countryCode": cc, "type": t})
                    return
        out({"found": False, "id": a.type})
        return

    if a.search:
        kw = a.search.lower()
        hits = []
        for cc, node in _ext()["countries"].items():
            for t in node.get("types", []):
                hay = (t.get("id", "") + t.get("type", "") + " ".join(t.get("aliases", []))).lower()
                if kw in hay:
                    hits.append({"countryCode": cc, "id": t["id"], "type": t["type"],
                                 "stay_single": t["stay_single"], "confidence": t["confidence"]})
        out({"found": bool(hits), "keyword": a.search, "matches": hits})
        return

    ap.print_help()
    sys.exit(1)


if __name__ == "__main__":
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    main()
