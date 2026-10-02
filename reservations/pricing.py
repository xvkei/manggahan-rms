"""
Pricing and refund rules for the Manggahan Sports Complex RMS.

This module has no Django imports on purpose, so the business rules can be
unit-tested on their own and explained easily in the paper.

Rules
-----
* Each hour is charged the court's daytime rate, or the evening rate from
  the evening start hour (default 6:00 PM) onward.
* Discounts do not stack. For every hour, only the biggest discount applies:
  either the player-type discount (student, member, senior/PWD) or an
  active off-peak promo.
* Loyalty points can be redeemed for one free hour. The free hour is the
  cheapest hour in the booking (after discounts).
* Equipment add-ons are added on top and are never discounted.
* Refund tiers: 24 hours or more before start = 100%, less than 24 hours but
  before start = 50%, after start / no-show = 0%. If the complex cancels
  (maintenance), the refund is always 100%.
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def money(value) -> Decimal:
    """Round any number to centavos."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def hour_rate(day_rate, evening_rate, hour: int, evening_start: int) -> Decimal:
    """Base price of one hour that starts at `hour` (24-hour clock)."""
    return money(evening_rate if hour >= evening_start else day_rate)


def build_quote(
    hours,
    day_rate,
    evening_rate,
    evening_start: int,
    type_discount_pct: int = 0,
    type_label: str = "",
    promo_by_hour=None,
    equipment_lines=None,
    redeem_free_hour: bool = False,
):
    """
    Compute the full price breakdown for a booking.

    hours            list of starting hours, e.g. [19, 20] for 7 to 9 PM
    promo_by_hour    dict {hour: (percent, promo_name)}
    equipment_lines  list of (name, quantity, unit_price)

    Returns a dict with Decimal values and a per-hour breakdown.
    """
    promo_by_hour = promo_by_hour or {}
    equipment_lines = equipment_lines or []

    hour_lines = []
    court_fee = Decimal("0")
    discount = Decimal("0")
    reasons = set()

    for h in sorted(hours):
        base = hour_rate(day_rate, evening_rate, h, evening_start)
        promo_pct, promo_name = promo_by_hour.get(h, (0, ""))
        if promo_pct > type_discount_pct:
            pct, reason = promo_pct, promo_name
        elif type_discount_pct > 0:
            pct, reason = type_discount_pct, type_label
        else:
            pct, reason = 0, ""
        hour_discount = money(base * Decimal(pct) / 100)
        if pct:
            reasons.add(f"{reason} {pct}%")
        court_fee += base
        discount += hour_discount
        hour_lines.append(
            {
                "hour": h,
                "base": base,
                "discount_pct": pct,
                "discount": hour_discount,
                "net": base - hour_discount,
                "is_evening": h >= evening_start,
                "promo": promo_pct > type_discount_pct and promo_pct > 0,
            }
        )

    free_hour_value = Decimal("0")
    if redeem_free_hour and hour_lines:
        free_hour_value = min(line["net"] for line in hour_lines)

    equipment_total = sum(
        (money(price) * int(qty) for _name, qty, price in equipment_lines if int(qty) > 0),
        Decimal("0"),
    )

    total = court_fee - discount - free_hour_value + equipment_total
    return {
        "hour_lines": hour_lines,
        "court_fee": money(court_fee),
        "discount": money(discount),
        "discount_note": ", ".join(sorted(reasons)),
        "free_hour_value": money(free_hour_value),
        "equipment_total": money(equipment_total),
        "total": money(max(total, Decimal("0"))),
    }


def refund_tier(hours_before_start: float, cancelled_by_complex: bool = False):
    """Return (tier_code, percent) for a cancellation."""
    if cancelled_by_complex:
        return "complex", 100
    if hours_before_start >= 24:
        return "full", 100
    if hours_before_start > 0:
        return "half", 50
    return "none", 0


def refund_amount(amount_paid, percent: int) -> Decimal:
    return money(Decimal(str(amount_paid)) * Decimal(percent) / 100)


def points_for(amount_paid, peso_per_point: int = 100) -> int:
    """Loyalty points earned: 1 point per peso_per_point pesos, rounded down."""
    if peso_per_point <= 0:
        return 0
    return int(Decimal(str(amount_paid)) // Decimal(peso_per_point))


def is_consecutive(hours) -> bool:
    hs = sorted(hours)
    return bool(hs) and all(b - a == 1 for a, b in zip(hs, hs[1:]))
