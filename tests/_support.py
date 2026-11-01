SUPPORTED_PROFILE = {
    "passport_nationality": "CN",
    "passport_type": "ordinary",
    "residence_status": "visitor",
}


def with_supported_profile(payload):
    out = dict(payload)
    for key, value in SUPPORTED_PROFILE.items():
        out.setdefault(key, value)
    # 既有的算法回归只验证停留规则，不是签证身份/有效期产品测试。
    # 明确补上短期访客与入境依据；签证实例再补远未来日期，V 系列单独验证真实冲突。
    visa_based = (
        bool(str(out.get("visa_type") or "").strip())
        or out.get("visa_days") not in (None, "")
        or (out.get("visa_source") or "dataset") != "dataset"
    )
    out.setdefault("visa_class", "short_stay")
    out.setdefault("entry_basis", "visa" if visa_based else "visa_free")
    if visa_based:
        out.setdefault("visa_valid_until", "2099-12-31")
        out.setdefault("visa_expiry_semantics", "entry_by")
        out.setdefault("admission_until", "2099-12-31")
    return out
