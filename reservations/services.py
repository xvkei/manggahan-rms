"""
Business logic shared by the player site, the admin screens, and the
background jobs. Views stay thin and call these functions.
"""
import random
import re
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone

from . import pricing
from .models import (
    ZERO,
    ActivityLog,
    MaintenanceBlock,
    Payment,
    PhoneVerification,
    Player,
    PlayerType,
    Promo,
    Refund,
    Reservation,
    SiteSettings,
    SmsMessage,
)

S = Reservation.Status


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def hour_label(hour):
    """19 -> '7:00 PM'"""
    hour = int(hour) % 24
    suffix = "AM" if hour < 12 else "PM"
    h12 = hour % 12 or 12
    return f"{h12}:00 {suffix}"


def range_label(start, end):
    return f"{hour_label(start)} to {hour_label(end)}"


def normalize_mobile(raw):
    """Accepts 0917 123 4567, +63 917 123 4567, 639171234567. Returns 09171234567 or None."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("63") and len(digits) == 12:
        digits = "0" + digits[2:]
    if len(digits) == 10 and digits.startswith("9"):
        digits = "0" + digits
    if len(digits) == 11 and digits.startswith("09"):
        return digits
    return None


def log(actor, action, reference=""):
    ActivityLog.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        action=action[:255],
        reference=reference[:60],
    )


def send_sms(mobile, body, kind="info"):
    """
    Demo SMS gateway. Stores the message in the outbox and prints it to the
    console. Swap this function for a real gateway (e.g. Semaphore) later.
    """
    if not mobile:
        return None
    msg = SmsMessage.objects.create(mobile=mobile, body=body, kind=kind)
    print(f"[SMS to {mobile}] {body}")
    return msg


def is_manager(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "staff_profile", None)
    return bool(profile and profile.role == "manager")


def is_staff_member(user):
    return user.is_authenticated and (user.is_superuser or hasattr(user, "staff_profile"))


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------
def blocking_reservations(court, day, exclude_id=None):
    qs = Reservation.objects.filter(court=court, date=day, status__in=Reservation.BLOCKING)
    if exclude_id:
        qs = qs.exclude(pk=exclude_id)
    return qs


def slot_states(court, day, exclude_id=None):
    """Return {hour: 'open' | 'taken' | 'maintenance' | 'past'} for one court and day."""
    cfg = SiteSettings.get()
    states = {h: "open" for h in cfg.hours}
    for r in blocking_reservations(court, day, exclude_id):
        for h in range(r.start_hour, r.end_hour):
            if h in states:
                states[h] = "taken"
    for b in MaintenanceBlock.objects.filter(court=court, date=day):
        for h in range(b.start_hour, b.end_hour):
            if h in states:
                states[h] = "maintenance"
    now = timezone.localtime()
    if day < now.date():
        return {h: "past" for h in states}
    if day == now.date():
        for h in states:
            if h <= now.hour and states[h] == "open":
                states[h] = "past"
    return states


def check_range(court, day, start, end, exclude_id=None, allow_past=False):
    """Return (ok, reason) for booking court on day from start to end."""
    cfg = SiteSettings.get()
    if not court.is_active:
        return False, "This court is not open for booking."
    if start < cfg.open_hour or end > cfg.close_hour or end <= start:
        return False, "Pick a time within operating hours."
    states = slot_states(court, day, exclude_id)
    for h in range(start, end):
        state = states.get(h, "past")
        if state == "past" and allow_past:
            continue
        if state != "open":
            label = {"taken": "already taken", "maintenance": "blocked for maintenance",
                     "past": "already past"}.get(state, "not available")
            return False, f"{hour_label(h)} is {label}."
    return True, ""


def promos_for(court, day, hours):
    """{hour: (percent, name)} for the best active promo on each hour."""
    result = {}
    for promo in Promo.objects.filter(is_active=True):
        for h in hours:
            if promo.applies(day, h, court.sport):
                if promo.percent > result.get(h, (0, ""))[0]:
                    result[h] = (promo.percent, promo.name)
    return result


def quote(court, day, start, end, player_type, equipment_lines=None, redeem_free_hour=False):
    cfg = SiteSettings.get()
    hours = list(range(start, end))
    return pricing.build_quote(
        hours=hours,
        day_rate=court.day_rate,
        evening_rate=court.evening_rate,
        evening_start=cfg.evening_start_hour,
        type_discount_pct=cfg.discount_for(player_type),
        type_label=PlayerType(player_type).label if player_type else "",
        promo_by_hour=promos_for(court, day, hours),
        equipment_lines=equipment_lines or [],
        redeem_free_hour=redeem_free_hour,
    )


def payment_deadline_for(start_dt, now=None):
    """Pay within N hours, but no later than M hours before the game."""
    cfg = SiteSettings.get()
    now = now or timezone.now()
    return min(now + timedelta(hours=cfg.pay_deadline_hours),
               start_dt - timedelta(hours=cfg.pay_cutoff_hours_before))


def manage_url(reservation, request=None):
    path = reverse("manage_booking", args=[reservation.manage_token])
    return request.build_absolute_uri(path) if request else path


# ---------------------------------------------------------------------------
# Phone verification (OTP)
# ---------------------------------------------------------------------------
def send_verification_code(mobile):
    code = f"{random.randint(0, 999999):06d}"
    PhoneVerification.objects.create(mobile=mobile, code=code)
    send_sms(mobile, f"Your Manggahan Sports Complex code is {code}. It expires in 10 minutes.", "otp")
    return code


def check_verification_code(mobile, code):
    """Return (ok, message)."""
    pv = PhoneVerification.objects.filter(mobile=mobile, verified_at__isnull=True).first()
    if not pv or pv.created_at < timezone.now() - timedelta(minutes=10):
        return False, "That code expired. Send a new one."
    if pv.attempts >= 5:
        return False, "Too many tries. Send a new code."
    if pv.code != (code or "").strip():
        pv.attempts += 1
        pv.save(update_fields=["attempts"])
        return False, "That code doesn't match. Check the SMS and try again."
    pv.verified_at = timezone.now()
    pv.save(update_fields=["verified_at"])
    return True, ""


# ---------------------------------------------------------------------------
# Payments, points
# ---------------------------------------------------------------------------
def award_points(reservation):
    cfg = SiteSettings.get()
    if reservation.points_awarded or not reservation.player.mobile:
        return 0
    pts = pricing.points_for(reservation.amount_paid, cfg.peso_per_point)
    if pts:
        player = reservation.player
        player.loyalty_points += pts
        player.save(update_fields=["loyalty_points"])
    reservation.points_awarded = True
    reservation.save(update_fields=["points_awarded"])
    return pts


@transaction.atomic
def record_payment(reservation, method, amount, actor=None, reference=""):
    amount = pricing.money(amount)
    Payment.objects.create(reservation=reservation, method=method, amount=amount,
                           reference=reference, received_by=actor if actor and actor.is_authenticated else None)
    reservation.amount_paid += amount
    reservation.payment_method = method
    if reservation.status == S.PENDING and reservation.is_fully_paid:
        reservation.status = S.CONFIRMED
        reservation.payment_deadline = None
    reservation.save()
    if reservation.is_fully_paid:
        award_points(reservation)
    return reservation


def confirmation_sms(reservation, request=None):
    r = reservation
    when = f"{r.date:%a, %b} {r.date.day}, {range_label(r.start_hour, r.end_hour)}"
    if r.status == S.PENDING:
        deadline = timezone.localtime(r.payment_deadline) if r.payment_deadline else None
        pay_note = f" Pay ₱{r.balance_due:,.2f} by {deadline:%b %d, %I:%M %p} or the slot is released." if deadline else ""
        body = f"Reserved: {r.court.name}, {when}. Ref {r.code}.{pay_note} Manage: {manage_url(r, request)}"
    else:
        body = f"Confirmed: {r.court.name}, {when}. Ref {r.code}. Show your QR at the front desk: {manage_url(r, request)}"
    send_sms(r.player.mobile, body, "confirmation")


# ---------------------------------------------------------------------------
# Cancellations and refunds
# ---------------------------------------------------------------------------
def refund_preview(reservation, now=None):
    now = now or timezone.now()
    hours_before = (reservation.start_dt - now).total_seconds() / 3600
    tier, pct = pricing.refund_tier(hours_before)
    return {
        "tier": tier,
        "percent": pct,
        "amount": pricing.refund_amount(reservation.amount_paid, pct),
        "hours_before": hours_before,
        "full_until": reservation.start_dt - timedelta(hours=24),
        "half_until": reservation.start_dt,
    }


@transaction.atomic
def request_cancel(reservation, actor=None):
    """Player cancels. Unpaid bookings cancel right away; paid ones go to the refund queue."""
    preview = refund_preview(reservation)
    if reservation.amount_paid <= 0:
        reservation.status = S.CANCELLED
        reservation.save(update_fields=["status"])
        log(actor, f"Cancelled unpaid booking, {reservation.court.name}", reservation.code)
        send_sms(reservation.player.mobile, f"Your booking {reservation.code} is cancelled.", "info")
        return None
    refund = Refund.objects.create(
        reservation=reservation,
        tier=preview["tier"],
        percent=preview["percent"],
        amount=preview["amount"],
        destination=reservation.get_payment_method_display() or "Original payment",
        hours_before=Decimal(str(round(preview["hours_before"], 1))),
    )
    reservation.status = S.CANCEL_REQUESTED
    reservation.save(update_fields=["status"])
    log(None, f"Cancellation request received, {preview['percent']}% refund computed", reservation.code)
    send_sms(reservation.player.mobile,
             f"Cancellation received for {reservation.code}. Refund of ₱{refund.amount:,.2f} is waiting for approval.",
             "info")
    return refund


@transaction.atomic
def decide_refund(refund, approve, actor, reason=""):
    r = refund.reservation
    refund.decided_at = timezone.now()
    refund.decided_by = actor
    refund.reason = reason
    if approve:
        refund.status = Refund.Status.APPROVED
        if refund.tier != Refund.Tier.DIFFERENCE:
            r.status = S.CANCELLED
        r.amount_paid = max(r.amount_paid - refund.amount, ZERO)
        r.save(update_fields=["status", "amount_paid"])
        log(actor, f"Approved refund ₱{refund.amount:,.2f} to {refund.destination}", r.code)
        send_sms(r.player.mobile,
                 f"Refund of ₱{refund.amount:,.2f} for {r.code} approved. Expect it in 3 to 5 business days.",
                 "info")
    else:
        refund.status = Refund.Status.DECLINED
        if refund.tier != Refund.Tier.DIFFERENCE:
            ok, _ = check_range(r.court, r.date, r.start_hour, r.end_hour, exclude_id=r.pk, allow_past=True)
            r.status = S.CONFIRMED if ok else S.CANCELLED
            r.save(update_fields=["status"])
        log(actor, f"Declined refund: {reason or 'no reason given'}", r.code)
        send_sms(r.player.mobile, f"Your refund request for {r.code} was declined. {reason}", "info")
    refund.save()


@transaction.atomic
def cancel_by_complex(reservation, actor, reason):
    """Complex cancels (maintenance, official use). Always a full refund, auto-approved."""
    r = reservation
    if r.amount_paid > 0:
        Refund.objects.create(
            reservation=r, tier=Refund.Tier.COMPLEX, percent=100, amount=r.amount_paid,
            destination=r.get_payment_method_display() or "Original payment",
            status=Refund.Status.APPROVED, decided_at=timezone.now(), decided_by=actor, reason=reason,
        )
    refunded = r.amount_paid
    r.amount_paid = ZERO
    r.status = S.CANCELLED
    r.notes = (r.notes + f"\nCancelled by complex: {reason}").strip()
    r.save()
    send_sms(r.player.mobile,
             f"Sorry, {r.court.name} is unavailable on {r.date:%b %d} ({reason}). "
             f"Booking {r.code} is cancelled with a full refund of ₱{refunded:,.2f}. Rebook anytime.",
             "info")
    return refunded


@transaction.atomic
def block_court(court, day, start, end, reason, actor):
    affected = list(
        Reservation.objects.filter(court=court, date=day, status__in=Reservation.BLOCKING,
                                   start_hour__lt=end, end_hour__gt=start)
    )
    MaintenanceBlock.objects.create(court=court, date=day, start_hour=start, end_hour=end,
                                    reason=reason, created_by=actor)
    total = ZERO
    for r in affected:
        total += cancel_by_complex(r, actor, reason)
    log(actor, f"Blocked {court.name}, {range_label(start, end)} ({reason}); "
               f"{len(affected)} booking(s) refunded ₱{total:,.2f}", f"{day:%b %d}")
    return affected, total


# ---------------------------------------------------------------------------
# Front desk actions
# ---------------------------------------------------------------------------
def check_in(reservation, actor):
    reservation.status = S.CHECKED_IN
    reservation.checked_in_at = timezone.now()
    reservation.save(update_fields=["status", "checked_in_at"])
    log(actor, f"Checked in {reservation.player.name}, {reservation.court.name}", reservation.code)


def mark_no_show(reservation, actor):
    reservation.status = S.NO_SHOW
    reservation.save(update_fields=["status"])
    player = reservation.player
    player.no_show_count += 1
    player.save(update_fields=["no_show_count"])
    log(actor, f"Marked no-show and opened {reservation.court.name}", reservation.code)


# ---------------------------------------------------------------------------
# Housekeeping (auto-release, reminders, completion)
# ---------------------------------------------------------------------------
def run_housekeeping(force=False):
    """
    Runs the automatic rules. Called by `python manage.py run_jobs` (cron) and,
    for the demo, lazily at most once a minute when pages load.
    """
    if not force and cache.get("rms_housekeeping_ran"):
        return {}
    cache.set("rms_housekeeping_ran", True, 60)
    now = timezone.now()
    released = reminded = completed = deadline_warned = 0

    # 1. Release unpaid bookings past their deadline.
    for r in Reservation.objects.filter(status=S.PENDING, payment_deadline__lt=now).select_related("player", "court"):
        r.status = S.RELEASED
        r.save(update_fields=["status"])
        released += 1
        log(None, f"Auto-released unpaid booking, {r.court.name} {range_label(r.start_hour, r.end_hour)}", r.code)
        send_sms(r.player.mobile, f"Booking {r.code} was released because it wasn't paid in time.", "info")

    # 2. Warn players 2 hours before their payment deadline.
    soon = now + timedelta(hours=2)
    for r in Reservation.objects.filter(status=S.PENDING, deadline_reminder_sent=False,
                                        payment_deadline__lte=soon, payment_deadline__gte=now):
        deadline = timezone.localtime(r.payment_deadline)
        send_sms(r.player.mobile, f"Reminder: pay ₱{r.balance_due:,.2f} for {r.code} by "
                                  f"{deadline:%I:%M %p} or the slot is released.", "reminder")
        r.deadline_reminder_sent = True
        r.save(update_fields=["deadline_reminder_sent"])
        deadline_warned += 1

    # 3. Game reminders about 3 hours before start.
    today = timezone.localdate()
    for r in Reservation.objects.filter(status=S.CONFIRMED, reminder_sent=False,
                                        date__gte=today, date__lte=today + timedelta(days=1)).select_related("player", "court"):
        if r.player.sms_reminders and now <= r.start_dt <= now + timedelta(hours=3):
            id_note = " Bring a valid ID for your discount." if r.player_type in (
                PlayerType.STUDENT, PlayerType.SENIOR_PWD) else ""
            send_sms(r.player.mobile, f"See you at {hour_label(r.start_hour)}: {r.court.name}. "
                                      f"Show your QR at the front desk.{id_note}", "reminder")
            r.reminder_sent = True
            r.save(update_fields=["reminder_sent"])
            reminded += 1

    # 4. Mark finished games as completed.
    for r in Reservation.objects.filter(status=S.CHECKED_IN, date__lte=today):
        if r.end_dt <= now:
            r.status = S.COMPLETED
            r.save(update_fields=["status"])
            completed += 1

    return {"released": released, "reminded": reminded, "completed": completed,
            "deadline_warned": deadline_warned}


# ---------------------------------------------------------------------------
# Players
# ---------------------------------------------------------------------------
def get_or_create_player(mobile, name, player_type=PlayerType.WALKIN, user=None):
    player = None
    if user is not None and getattr(user, "player", None):
        player = user.player
    elif mobile:
        player = Player.objects.filter(mobile=mobile).first()
    if player is None:
        player = Player.objects.create(mobile=mobile or None, name=name or "Guest", player_type=player_type, user=user)
    else:
        changed = []
        if name and player.name != name:
            player.name = name
            changed.append("name")
        if player_type and player.player_type != player_type:
            player.player_type = player_type
            changed.append("player_type")
        if changed:
            player.save(update_fields=changed)
    return player


def todays_collections(day=None):
    day = day or timezone.localdate()
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = start + timedelta(days=1)
    total = sum((p.amount for p in Payment.objects.filter(created_at__gte=start, created_at__lt=end)), ZERO)
    return total


def to_collect_queryset():
    """Bookings with money still to collect: unpaid, or paid partly (e.g. after a reschedule)."""
    return Reservation.objects.filter(
        Q(status=S.PENDING) | Q(status__in=[S.CONFIRMED, S.CHECKED_IN], amount_paid__lt=F("total"))
    ).select_related("player", "court")
