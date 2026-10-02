from decimal import Decimal, InvalidOperation

from django import template

from .. import services

register = template.Library()

STATUS_CLASSES = {
    "pending": "st-pending",
    "confirmed": "st-confirmed",
    "checked_in": "st-in",
    "completed": "st-done",
    "cancel_requested": "st-cancelreq",
    "cancelled": "st-cancelled",
    "no_show": "st-noshow",
    "released": "st-cancelled",
}


@register.filter
def peso(value, decimals=2):
    """1234.5 -> ₱1,234.50"""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    decimals = int(decimals)
    return f"₱{amount:,.{decimals}f}"


@register.filter
def hour12(value):
    try:
        return services.hour_label(int(value))
    except (TypeError, ValueError):
        return value


@register.filter
def hour_short(value):
    """19 -> '7 PM'"""
    try:
        h = int(value) % 24
    except (TypeError, ValueError):
        return value
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


@register.simple_tag
def hour_range(start, end):
    return services.range_label(start, end)


@register.filter
def status_class(value):
    return STATUS_CLASSES.get(str(value), "st-done")


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def mul(value, arg):
    try:
        return Decimal(str(value)) * Decimal(str(arg))
    except (InvalidOperation, TypeError, ValueError):
        return ""
