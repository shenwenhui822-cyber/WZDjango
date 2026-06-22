from django import template

register = template.Library()


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
