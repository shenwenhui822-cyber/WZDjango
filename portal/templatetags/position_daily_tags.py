from django import template

register = template.Library()


def _normalize_wind_date(value) -> str | None:
    if value is None or value == "":
        return None
    s = str(value).strip().replace("-", "")
    return s if len(s) == 8 and s.isdigit() else None


@register.filter
def get_item(d, key):
    if isinstance(d, dict):
        return d.get(key, "")
    return ""


@register.filter
def pct(value, digits=2):
    try:
        return f"{float(value) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


@register.filter
def wind_dt_fmt(value):
    """Wind 日期 YYYYMMDD → YYYY-MM-DD。"""
    s = _normalize_wind_date(value)
    if not s:
        return value if value not in (None, "") else "—"
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


@register.filter
def wind_date_before(value, snapshot_date):
    """Wind 数据日期是否早于快照日（数据落后）。"""
    data = _normalize_wind_date(value)
    snap = _normalize_wind_date(snapshot_date)
    if not data or not snap:
        return False
    return data < snap
