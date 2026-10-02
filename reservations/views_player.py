"""Player-facing pages: book a court, verify mobile, pay, manage, accounts."""
import calendar
import random
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import pricing, services
from .models import (
    ONLINE_METHODS,
    Court,
    Item,
    PaymentMethod,
    PhoneVerification,
    Player,
    PlayerType,
    Refund,
    Reservation,
    ReservationItem,
    SiteSettings,
    Sport,
)

S = Reservation.Status
User = get_user_model()

PLAYER_TYPE_NOTES = {
    "walkin": "Regular rate",
    "member": "Member rate, priority booking",
    "student": "School ID at check-in",
    "team": "Block bookings, tournaments",
    "senior_pwd": "Valid ID at check-in",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _current_player(request):
    if request.user.is_authenticated:
        return getattr(request.user, "player", None)
    return None


def _get_hold(request):
    """Return the slot hold stored in the session, or None if missing/expired."""
    hold = request.session.get("hold")
    if not hold:
        return None
    if hold.get("expires", 0) < timezone.now().timestamp():
        request.session.pop("hold", None)
        return None
    court = Court.objects.filter(pk=hold["court"], is_active=True).first()
    day = parse_date(hold["date"])
    if not court or not day:
        return None
    expires = datetime.fromtimestamp(hold["expires"], tz=timezone.get_current_timezone())
    return {"court": court, "date": day, "start": hold["start"], "end": hold["end"],
            "expires": expires, "hours": hold["end"] - hold["start"]}


def _aware(day, hour):
    return timezone.make_aware(datetime.combine(day, time(hour)), timezone.get_current_timezone())


def _safe_next(request, fallback):
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return nxt
    return fallback


def _type_options(cfg):
    options = []
    for value, label in PlayerType.choices:
        pct = cfg.discount_for(value)
        note = PLAYER_TYPE_NOTES.get(str(value), "")
        if pct:
            note = f"{pct}% off · {note}"
        options.append({"value": value, "label": label, "note": note, "pct": pct})
    return options


# ---------------------------------------------------------------------------
# Step 1: choose a slot
# ---------------------------------------------------------------------------
def home(request):
    services.run_housekeeping()
    cfg = SiteSettings.get()
    today = timezone.localdate()
    max_day = today + timedelta(days=60)
    courts = list(Court.objects.filter(is_active=True))

    sports = []
    for code, label in Sport.choices:
        group = [c for c in courts if c.sport == code]
        if group:
            unit = "tables" if code == Sport.TABLE_TENNIS else ("court" if len(group) == 1 else "courts")
            sports.append({"code": code, "label": label, "count": len(group), "unit": unit,
                           "from_rate": min(c.day_rate for c in group)})
    codes = [s["code"] for s in sports]
    sport = request.GET.get("sport")
    if sport not in codes:
        sport = Sport.BADMINTON if Sport.BADMINTON in codes else (codes[0] if codes else "")

    day = parse_date(request.GET.get("date") or "") or today
    day = min(max(day, today), max_day)

    sport_courts = [c for c in courts if c.sport == sport]
    for c in sport_courts:
        states = services.slot_states(c, day)
        c.open_count = sum(1 for v in states.values() if v == "open")
    court = next((c for c in sport_courts if str(c.pk) == request.GET.get("court")), None)
    if court is None and sport_courts:
        court = next((c for c in sport_courts if c.open_count), sport_courts[0])

    slots = []
    if court:
        states = services.slot_states(court, day)
        promos = services.promos_for(court, day, cfg.hours)
        for h in cfg.hours:
            base = pricing.hour_rate(court.day_rate, court.evening_rate, h, cfg.evening_start_hour)
            pct = promos.get(h, (0, ""))[0]
            price = base - pricing.money(base * pct / 100)
            slots.append({"hour": h, "state": states[h], "price": price, "promo": pct > 0})

    first = day.replace(day=1)
    weeks = []
    for week in calendar.Calendar(firstweekday=6).monthdatescalendar(day.year, day.month):
        weeks.append([
            {"date": d, "in_month": d.month == day.month, "disabled": d < today or d > max_day,
             "selected": d == day, "today": d == today}
            for d in week
        ])
    prev_month = (first - timedelta(days=1)).replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)

    rate_rows = []
    for code, label in Sport.choices:
        group = [c for c in courts if c.sport == code]
        if group:
            rate_rows.append({"label": label, "day": min(c.day_rate for c in group),
                              "evening": min(c.evening_rate for c in group),
                              "note": group[0].unit_note})

    any_promo = any(s["promo"] for s in slots)
    return render(request, "player/book.html", {
        "cfg": cfg, "sports": sports, "sport": sport, "sport_courts": sport_courts, "court": court,
        "day": day, "today": today, "slots": slots, "weeks": weeks,
        "prev_month": prev_month if prev_month >= today.replace(day=1) else None,
        "next_month": next_month if next_month <= max_day else None,
        "rate_rows": rate_rows, "any_promo": any_promo,
        "evening_label": services.hour_label(cfg.evening_start_hour),
        "slot_data": ({
            "prices": {s["hour"]: str(s["price"]) for s in slots},
            "labels": {h: services.hour_label(h) for h in range(cfg.open_hour, cfg.close_hour + 1)},
        }),
    })


@require_POST
def hold_slots(request):
    cfg = SiteSettings.get()
    court = get_object_or_404(Court, pk=request.POST.get("court"), is_active=True)
    day = parse_date(request.POST.get("date", ""))
    back = f"{reverse('home')}?sport={court.sport}&court={court.pk}&date={day or ''}"
    try:
        hours = sorted({int(h) for h in request.POST.getlist("hours")})
    except ValueError:
        hours = []
    if not day or not hours:
        messages.error(request, "Pick at least one open time slot.")
        return redirect(back)
    if not pricing.is_consecutive(hours):
        messages.error(request, "Pick time slots that are next to each other.")
        return redirect(back)
    ok, reason = services.check_range(court, day, hours[0], hours[-1] + 1)
    if not ok:
        messages.error(request, reason)
        return redirect(back)
    request.session["hold"] = {
        "court": court.pk, "date": day.isoformat(), "start": hours[0], "end": hours[-1] + 1,
        "expires": (timezone.now() + timedelta(minutes=cfg.hold_minutes)).timestamp(),
    }
    if _current_player(request) or request.session.get("verified_mobile"):
        return redirect("booking_details")
    return redirect("verify_mobile")


# ---------------------------------------------------------------------------
# Step 2: verify mobile (guest booking, no account needed)
# ---------------------------------------------------------------------------
def verify_mobile(request):
    details_url = reverse("booking_details")
    next_url = _safe_next(request, details_url)
    hold = _get_hold(request)
    if next_url == details_url and not hold:
        messages.error(request, "Your held slot expired. Pick a time again.")
        return redirect("home")
    if next_url == details_url and _current_player(request):
        return redirect(details_url)

    ctx = {"next": next_url, "hold": hold, "step": "phone",
           "mobile": request.session.get("otp_mobile", ""), "error": "", "demo_code": None}

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "send":
            mobile = services.normalize_mobile(request.POST.get("mobile"))
            if not mobile:
                ctx.update(error="Enter a valid mobile number, like 0917 123 4567.",
                           mobile=request.POST.get("mobile", ""))
            else:
                recent = PhoneVerification.objects.filter(
                    mobile=mobile, created_at__gte=timezone.now() - timedelta(minutes=10)).count()
                request.session["otp_mobile"] = mobile
                ctx.update(step="code", mobile=mobile)
                if recent >= 3:
                    ctx["error"] = "We already sent 3 codes. Use the latest one or wait a few minutes."
                else:
                    services.send_verification_code(mobile)
        elif action == "check":
            mobile = request.session.get("otp_mobile")
            code = "".join(request.POST.getlist("digit")) or request.POST.get("code", "")
            if not mobile:
                ctx["error"] = "Enter your mobile number first."
            else:
                ok, msg = services.check_verification_code(mobile, code)
                if ok:
                    request.session["verified_mobile"] = mobile
                    messages.success(request, "Number verified.")
                    return redirect(next_url)
                ctx.update(step="code", mobile=mobile, error=msg)
        elif action == "change":
            ctx["step"] = "phone"

    if ctx["step"] == "code" and settings.RMS_DEMO_MODE:
        latest = PhoneVerification.objects.filter(mobile=ctx["mobile"], verified_at__isnull=True).first()
        ctx["demo_code"] = latest.code if latest else None
    return render(request, "player/verify.html", ctx)


# ---------------------------------------------------------------------------
# Step 3: details, add-ons, payment method
# ---------------------------------------------------------------------------
def details(request):
    hold = _get_hold(request)
    if not hold:
        messages.error(request, "Your held slot expired. Pick a time again.")
        return redirect("home")
    player = _current_player(request)
    mobile = player.mobile if player else request.session.get("verified_mobile")
    if not mobile:
        return redirect("verify_mobile")
    if player is None:
        player = Player.objects.filter(mobile=mobile).first()

    cfg = SiteSettings.get()
    court, day, start, end = hold["court"], hold["date"], hold["start"], hold["end"]
    items = list(Item.objects.filter(is_active=True, bookable_online=True)
                 .filter(Q(sport="") | Q(sport=court.sport)))
    start_dt = _aware(day, start)
    deadline = services.payment_deadline_for(start_dt)
    desk_allowed = deadline > timezone.now() + timedelta(minutes=30)
    free_hour_ready = bool(player and player.loyalty_points >= cfg.points_per_free_hour)

    form = {
        "name": player.name if player else "",
        "player_type": player.player_type if player else PlayerType.WALKIN,
        "players": 2,
        "method": PaymentMethod.GCASH,
        "reminders": player.sms_reminders if player else True,
        "redeem": False,
        "agree": False,
        "qty": {i.pk: 0 for i in items},
    }
    errors = []

    if request.method == "POST":
        form["name"] = request.POST.get("name", "").strip()[:120]
        form["player_type"] = request.POST.get("player_type") if request.POST.get("player_type") in PlayerType.values else PlayerType.WALKIN
        try:
            form["players"] = max(1, min(40, int(request.POST.get("players") or 1)))
        except ValueError:
            form["players"] = 1
        method = request.POST.get("method")
        form["method"] = method if method in [*ONLINE_METHODS, PaymentMethod.DESK] else PaymentMethod.GCASH
        form["reminders"] = request.POST.get("reminders") == "on"
        form["redeem"] = request.POST.get("redeem") == "on" and free_hour_ready
        form["agree"] = request.POST.get("agree") == "on"
        for i in items:
            try:
                form["qty"][i.pk] = max(0, min(20, int(request.POST.get(f"item_{i.pk}") or 0)))
            except ValueError:
                form["qty"][i.pk] = 0

        if not form["name"]:
            errors.append("Enter the name of the person booking.")
        if not form["agree"]:
            errors.append("Agree to the cancellation policy to continue.")
        if form["method"] == PaymentMethod.DESK and not desk_allowed:
            errors.append("Your game starts soon, so please pay online to confirm it.")
        if form["player_type"] == PlayerType.MEMBER and not (player and player.is_active_member):
            errors.append("We couldn't find an active membership for this number. "
                          "Choose another player type, or get a membership at the front desk.")
        ok, reason = services.check_range(court, day, start, end)
        if not ok:
            errors.append(f"Sorry, {reason} Pick another time.")

        if not errors:
            equipment_lines = [(i.name, form["qty"][i.pk], i.price) for i in items if form["qty"][i.pk]]
            q = services.quote(court, day, start, end, form["player_type"], equipment_lines, form["redeem"])
            with transaction.atomic():
                if player is None:
                    player = Player.objects.create(mobile=mobile, name=form["name"],
                                                   player_type=form["player_type"])
                else:
                    player.name = form["name"]
                    if form["player_type"] != PlayerType.MEMBER or player.is_active_member:
                        player.player_type = form["player_type"]
                player.sms_reminders = form["reminders"]
                player.save()

                is_desk = form["method"] == PaymentMethod.DESK
                res = Reservation.objects.create(
                    player=player, court=court, date=day, start_hour=start, end_hour=end,
                    player_type=form["player_type"], players_count=form["players"],
                    court_fee=q["court_fee"], discount=q["discount"], discount_note=q["discount_note"],
                    free_hour_value=q["free_hour_value"], equipment_total=q["equipment_total"],
                    total=q["total"], payment_method=form["method"], status=S.PENDING,
                    source=Reservation.Source.ONLINE,
                    payment_deadline=deadline if is_desk else timezone.now() + timedelta(minutes=15),
                )
                for i in items:
                    if form["qty"][i.pk]:
                        ReservationItem.objects.create(reservation=res, item=i, quantity=form["qty"][i.pk],
                                                       unit_price=i.price)
                if form["redeem"]:
                    player.loyalty_points -= cfg.points_per_free_hour
                    player.save(update_fields=["loyalty_points"])
                    res.points_redeemed = cfg.points_per_free_hour
                if res.total <= 0:
                    res.status = S.CONFIRMED
                    res.payment_deadline = None
                res.save()
                request.session.pop("hold", None)
                services.log(None, f"Online booking created, {court.name} "
                                   f"{services.range_label(start, end)}", res.code)

            if res.status == S.CONFIRMED or is_desk:
                services.confirmation_sms(res, request)
                return redirect(f"{reverse('manage_booking', args=[res.manage_token])}?new=1")
            return redirect("pay", token=res.manage_token)

    equipment_lines = [(i.name, form["qty"][i.pk], i.price) for i in items if form["qty"][i.pk]]
    q = services.quote(court, day, start, end, form["player_type"], equipment_lines, form["redeem"])
    promos = services.promos_for(court, day, list(range(start, end)))
    price_data = {
        "hours": [
            {"base": str(pricing.hour_rate(court.day_rate, court.evening_rate, h, cfg.evening_start_hour)),
             "promo": promos.get(h, (0, ""))[0], "promoName": promos.get(h, (0, ""))[1]}
            for h in range(start, end)
        ],
        "typePct": {v: cfg.discount_for(v) for v in PlayerType.values},
        "typeLabel": dict(PlayerType.choices),
        "items": {str(i.pk): str(i.price) for i in items},
    }
    for i in items:
        i.qty = form["qty"][i.pk]
    return render(request, "player/details.html", {
        "hold": hold, "cfg": cfg, "player": player, "mobile": mobile, "items": items, "form": form,
        "errors": errors, "q": q, "type_options": _type_options(cfg), "desk_allowed": desk_allowed,
        "deadline": deadline, "free_hour_ready": free_hour_ready,
        "full_refund_until": start_dt - timedelta(hours=24), "grace": cfg.grace_minutes,
        "online_methods": [(m.value, m.label) for m in ONLINE_METHODS],
        "price_data": price_data,
    })


# ---------------------------------------------------------------------------
# Payment (simulated gateway)
# ---------------------------------------------------------------------------
def pay(request, token):
    res = get_object_or_404(Reservation.objects.select_related("court", "player"), manage_token=token)
    manage = reverse("manage_booking", args=[res.manage_token])
    if res.balance_due <= 0:
        return redirect(manage)
    if res.status not in (S.PENDING, S.CONFIRMED, S.CHECKED_IN):
        messages.error(request, "This booking can't be paid anymore.")
        return redirect(manage)

    method = request.POST.get("method") or request.GET.get("method") or res.payment_method
    if method not in ONLINE_METHODS:
        method = PaymentMethod.GCASH
    deadline = services.payment_deadline_for(res.start_dt)
    desk_allowed = deadline > timezone.now() + timedelta(minutes=30)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "pay":
            amount = res.balance_due
            services.record_payment(res, method, amount, reference=f"SIM-{random.randint(10**8, 10**9 - 1)}")
            services.log(None, f"Online payment received via {PaymentMethod(method).label}, ₱{amount:,.2f}", res.code)
            services.confirmation_sms(res, request)
            return redirect(f"{manage}?new=1")
        if action == "fail":
            res.payment_deadline = max(res.payment_deadline or timezone.now(), timezone.now() + timedelta(minutes=15))
            res.save(update_fields=["payment_deadline"])
            messages.error(request, "Payment didn't go through. Try again, or choose another method.")
            return redirect(f"{reverse('pay', args=[res.manage_token])}?method={method}")
        if action == "switch_desk" and desk_allowed and res.status == S.PENDING:
            res.payment_method = PaymentMethod.DESK
            res.payment_deadline = deadline
            res.save(update_fields=["payment_method", "payment_deadline"])
            services.confirmation_sms(res, request)
            return redirect(f"{manage}?new=1")

    return render(request, "player/pay.html", {
        "res": res, "method": method, "method_label": PaymentMethod(method).label,
        "online_methods": [(m.value, m.label) for m in ONLINE_METHODS],
        "desk_allowed": desk_allowed and res.status == S.PENDING, "deadline": deadline,
    })


# ---------------------------------------------------------------------------
# Confirmation / manage booking (link sent by SMS)
# ---------------------------------------------------------------------------
def manage_booking(request, token):
    res = get_object_or_404(Reservation.objects.select_related("court", "player"), manage_token=token)
    cfg = SiteSettings.get()
    return render(request, "player/manage.html", {
        "res": res,
        "is_new": request.GET.get("new") == "1",
        "items": res.items.select_related("item"),
        "refunds": res.refunds.all(),
        "full_refund_until": res.start_dt - timedelta(hours=24),
        "show_account_prompt": not request.user.is_authenticated and not res.player.user_id,
        "points_this": pricing.points_for(res.total, cfg.peso_per_point),
        "qr_text": request.build_absolute_uri(reverse("manage_booking", args=[res.manage_token])),
        "grace": cfg.grace_minutes,
        "is_discount_type": res.player_type in (PlayerType.STUDENT, PlayerType.SENIOR_PWD),
    })


def cancel_booking(request, token):
    res = get_object_or_404(Reservation.objects.select_related("court", "player"), manage_token=token)
    manage = reverse("manage_booking", args=[res.manage_token])
    if not res.can_cancel:
        messages.error(request, "This booking can't be cancelled online anymore. Talk to the front desk.")
        return redirect(manage)
    preview = services.refund_preview(res)
    if request.method == "POST":
        refund = services.request_cancel(res)
        if refund:
            messages.success(request, f"Cancellation sent. Your refund of ₱{refund.amount:,.2f} is waiting for approval.")
        else:
            messages.success(request, "Booking cancelled.")
        return redirect(manage)
    return render(request, "player/cancel.html", {"res": res, "preview": preview})


def reschedule_booking(request, token):
    res = get_object_or_404(Reservation.objects.select_related("court", "player"), manage_token=token)
    manage = reverse("manage_booking", args=[res.manage_token])
    if not res.can_reschedule:
        messages.error(request, "This booking can't be rescheduled online. Talk to the front desk.")
        return redirect(manage)
    cfg = SiteSettings.get()
    today = timezone.localdate()
    day = parse_date(request.POST.get("date") or request.GET.get("date") or "") or res.date
    day = min(max(day, today), today + timedelta(days=60))
    dur = res.hours
    states = services.slot_states(res.court, day, exclude_id=res.pk)
    starts = [h for h in cfg.hours
              if h + dur <= cfg.close_hour and all(states.get(x) == "open" for x in range(h, h + dur))]

    if request.method == "POST":
        try:
            start = int(request.POST.get("start", ""))
        except ValueError:
            start = None
        if start not in starts:
            messages.error(request, "Pick one of the open start times.")
        else:
            equipment_lines = [(ri.item.name, ri.quantity, ri.unit_price) for ri in res.items.select_related("item")]
            q = services.quote(res.court, day, start, start + dur, res.player_type, equipment_lines,
                               redeem_free_hour=res.points_redeemed > 0)
            old_label = f"{res.date:%b} {res.date.day}, {services.range_label(res.start_hour, res.end_hour)}"
            with transaction.atomic():
                res.date, res.start_hour, res.end_hour = day, start, start + dur
                res.court_fee, res.discount = q["court_fee"], q["discount"]
                res.discount_note, res.free_hour_value = q["discount_note"], q["free_hour_value"]
                res.equipment_total, res.total = q["equipment_total"], q["total"]
                res.reminder_sent = False
                if res.status == S.PENDING and res.payment_method == PaymentMethod.DESK:
                    res.payment_deadline = services.payment_deadline_for(res.start_dt)
                res.save()
                excess = res.amount_paid - res.total
                if excess > 0:
                    Refund.objects.create(reservation=res, tier=Refund.Tier.DIFFERENCE, percent=100, amount=excess,
                                          destination=res.get_payment_method_display() or "Original payment")
                new_label = f"{day:%b} {day.day}, {services.range_label(start, start + dur)}"
                services.log(None, f"Rescheduled from {old_label} to {new_label}", res.code)
            services.send_sms(res.player.mobile, f"Booking {res.code} moved to {new_label}. "
                                                 f"Your QR code stays the same.", "info")
            msg = f"Moved to {new_label}."
            if res.balance_due > 0 and res.status != S.PENDING:
                msg += f" Pay the ₱{res.balance_due:,.2f} difference at the front desk."
            elif excess > 0:
                msg += f" The ₱{excess:,.2f} difference will be refunded after approval."
            messages.success(request, msg)
            return redirect(manage)

    day_options = [today + timedelta(days=i) for i in range(14)]
    return render(request, "player/reschedule.html", {
        "res": res, "day": day, "starts": starts, "dur": dur, "day_options": day_options,
    })


# ---------------------------------------------------------------------------
# Accounts (optional, for regulars)
# ---------------------------------------------------------------------------
@login_required(login_url="player_login")
def my_reservations(request):
    player = getattr(request.user, "player", None)
    if player is None:
        if services.is_staff_member(request.user):
            return redirect("staff_schedule")
        messages.error(request, "This account has no player profile.")
        return redirect("home")
    cfg = SiteSettings.get()
    tab = request.GET.get("tab", "upcoming")
    qs = player.reservations.select_related("court").order_by("date", "start_hour")
    now_date = timezone.localdate()
    if tab == "past":
        rows = qs.filter(status__in=[S.COMPLETED, S.NO_SHOW, S.CHECKED_IN, S.CONFIRMED], date__lt=now_date).order_by("-date", "-start_hour")
    elif tab == "cancelled":
        rows = qs.filter(status__in=[S.CANCELLED, S.CANCEL_REQUESTED, S.RELEASED]).order_by("-date")
    else:
        tab = "upcoming"
        rows = [r for r in qs.filter(status__in=[S.PENDING, S.CONFIRMED, S.CHECKED_IN], date__gte=now_date)
                if r.end_dt > timezone.now()]
    upcoming_count = sum(
        1 for r in qs.filter(status__in=[S.PENDING, S.CONFIRMED, S.CHECKED_IN], date__gte=now_date)
        if r.end_dt > timezone.now()
    )
    return render(request, "player/my_reservations.html", {
        "player": player, "rows": rows, "tab": tab, "upcoming_count": upcoming_count,
        "free_hours": player.loyalty_points // cfg.points_per_free_hour if cfg.points_per_free_hour else 0,
        "cfg": cfg,
    })


def player_login(request):
    if request.user.is_authenticated:
        return redirect("staff_schedule" if services.is_staff_member(request.user) else "my_reservations")
    error = ""
    mobile_raw = ""
    if request.method == "POST":
        mobile_raw = request.POST.get("mobile", "")
        username = services.normalize_mobile(mobile_raw) or mobile_raw.strip()
        user = authenticate(request, username=username, password=request.POST.get("password", ""))
        if user:
            login(request, user)
            if services.is_staff_member(user) and not hasattr(user, "player"):
                return redirect("staff_schedule")
            return redirect(_safe_next(request, reverse("my_reservations")))
        error = "That mobile number and password don't match."
    return render(request, "player/login.html", {"error": error, "mobile": mobile_raw,
                                                 "next": request.GET.get("next", "")})


def player_signup(request):
    verified = request.session.get("verified_mobile")
    if not verified:
        messages.info(request, "First, confirm your mobile number with a 6-digit code.")
        return redirect(f"{reverse('verify_mobile')}?next={reverse('player_signup')}")
    existing = Player.objects.filter(mobile=verified).first()
    error = ""
    name = existing.name if existing else ""
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:120]
        pw1, pw2 = request.POST.get("password", ""), request.POST.get("password2", "")
        if User.objects.filter(username=verified).exists():
            error = "An account with this number already exists. Sign in instead."
        elif not name:
            error = "Enter your name."
        elif len(pw1) < 6:
            error = "Use at least 6 characters for your password."
        elif pw1 != pw2:
            error = "The two passwords don't match."
        else:
            with transaction.atomic():
                user = User.objects.create_user(username=verified, password=pw1, first_name=name)
                if existing:
                    existing.user = user
                    existing.name = name
                    existing.save(update_fields=["user", "name"])
                else:
                    Player.objects.create(user=user, mobile=verified, name=name)
            login(request, user)
            messages.success(request, "Account created. Your bookings with this number are all here now.")
            return redirect("my_reservations")
    return render(request, "player/signup.html", {"mobile": verified, "name": name, "error": error})


@require_POST
def logout_view(request):
    was_staff = services.is_staff_member(request.user)
    logout(request)
    return redirect("staff_login" if was_staff else "home")
