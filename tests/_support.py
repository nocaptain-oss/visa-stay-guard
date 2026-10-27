SUPPORTED_PROFILE = {
    "passport_nationality": "CN",
    "passport_type": "ordinary",
    "residence_status": "visitor",
}


def with_supported_profile(payload):
    out = dict(payload)
    for key, value in SUPPORTED_PROFILE.items():
        out.setdefault(key, value)
    return out
