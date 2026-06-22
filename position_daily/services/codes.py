def wind_to_code_rq(code_wind: str) -> str:
    """600100.SH → 600100.XSHG"""
    if not code_wind or "." not in code_wind:
        return code_wind
    sym, exch = code_wind.upper().split(".", 1)
    suffix = "XSHG" if exch == "SH" else "XSHE"
    return f"{sym}.{suffix}"
