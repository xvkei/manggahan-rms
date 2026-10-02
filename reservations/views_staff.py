"""Admin-side pages for front desk staff and managers."""
import csv
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import pricing, services
from .models import (
    ZERO,
    ActivityLog,
    Court,
    Item,
    LeagueBooking,
    MaintenanceBlock,
    MembershipPlan,
    Payment,
    PaymentMethod,
    Player,
    PlayerPackage,
    PlayerType,
    Promo,
    Refund,
    Reservation,
    Sale,
    SaleLine,
    SiteSettings,
    SmsMessage,
    Sport,
    StaffProfile,
)

S = Reservation.Status
User = get_user_model()
DESK_METHODS = [PaymentMethod.CASH, PaymentMethod.GCASH, PaymentMethod.MAYA, PaymentMethod.CARD]


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------
def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('staff_login')}?next={request.path}")
        if not services.is_staff_member(request.user):
            messages.error(request, "That page is for complex staff only.")
            return redirect("home")
        return view(request, *args, **kwargs)
    return wrapper


def manager_required(view):
    @wraps(view)
    @staff_required
    def wrapper(request, *args, **kwargs):
        if not services.is_manager(request.user):
            messages.error(request, "Only managers can open that page.")
            return redirect("staff_schedule")
        return view(request, *args, **kwargs)
    return wrapper


def staff_login(request):
    if request.user.is_authenticated and services.is_staff_member(request.user):
        return redirect("staff_schedule")
    error = ""
    if request.method == "POST":
        user = authenticate(request, username=request.POST.get("username", "").strip(),
                            password=request.POST.get("password", ""))
        if user and services.is_staff_member(user):
            login(request, user)
            services.log(user, "Signed in")
            nxt = request.GET.get("next", "")
            return redirect(nxt if nxt.startswith("/staff") else reverse("staff_schedule"))
        error = "That username and password don't match a staff account."
    return render(request, "staff/login.html", {"error": error})


def _dec(value, default=None):
    try:
        return pricing.money(Decimal(str(value).replace(",", "").replace("₱", "").strip()))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Today's schedule
# ---------------------------------------------------------------------------
@staff_required
def schedule(request):
    services.run_housekeeping()
    cfg = SiteSettings.get()
    today = timezone.localdate()
    day = parse_date(request.GET.get("date") or "") or today
    sport = request.GET.get("sport", "")
    courts = Court.objects.filter(is_active=True)
    if sport in Sport.values:
        courts = courts.filter(sport=sport)
    now = timezone.now()
    hours = cfg.hours
    sel_code = request.GET.get("sel", "")

    day_res = list(Reservation.objects.filter(
        date=day, status__in=[S.PENDING, S.CONFIRMED, S.CHECKED_IN, S.COMPLETED]
    ).select_related("player", "court"))
    blocks = list(MaintenanceBlock.objects.filter(date=day))

    rows = []
    flagged = []
    for c in courts:
        items = []
        for r in (x for x in day_res if x.court_id == c.pk):
            flag = r.possible_no_show(now, cfg.grace_minutes)
            if flag:
                flagged.append(r)
            if r.status == S.COMPLETED:
                cls, sub = "b-done", "Done"
            elif r.status == S.CHECKED_IN:
                cls, sub = "b-in", f"In · {services.range_label(r.start_hour, r.end_hour)}"
            elif flag:
                cls, sub = "b-flag", "No-show?"
            elif r.status == S.PENDING:
                cls, sub = "b-pending", f"Unpaid · {services.range_label(r.start_hour, r.end_hour)}"
            else:
                cls, sub = "b-confirmed", services.range_label(r.start_hour, r.end_hour)
            if r.balance_due > 0 and r.status in (S.CONFIRMED, S.CHECKED_IN):
                sub = f"Balance due · {sub}"
            items.append({
                "col_start": max(r.start_hour, cfg.open_hour) - cfg.open_hour + 2,
                "col_end": min(r.end_hour, cfg.close_hour) - cfg.open_hour + 2,
                "cls": cls, "title": r.player.name, "sub": sub, "code": r.code,
                "selected": r.code == sel_code,
            })
        for b in (x for x in blocks if x.court_id == c.pk):
            items.append({
                "col_start": max(b.start_hour, cfg.open_hour) - cfg.open_hour + 2,
                "col_end": min(b.end_hour, cfg.close_hour) - cfg.open_hour + 2,
                "cls": "b-maint", "title": b.reason, "sub": "Maintenance", "code": "", "selected": False,
            })
        rows.append({"court": c, "items": items})

    now_pct = None
    local_now = timezone.localtime()
    if day == today and cfg.open_hour <= local_now.hour < cfg.close_hour:
        now_pct = ((local_now.hour - cfg.open_hour) + local_now.minute / 60) / len(hours)

    selected = None
    if sel_code:
        selected = Reservation.objects.select_related("player", "court").filter(code=sel_code).first()
    if selected is None and flagged:
        selected = flagged[0]

    sel_flag = bool(selected and selected.possible_no_show(now, cfg.grace_minutes))
    no_show_allowed = bool(
        selected and selected.status == S.CONFIRMED
        and now >= selected.start_dt + timedelta(minutes=cfg.grace_minutes)
    )
    to_collect = services.to_collect_queryset().filter(date__gte=today).order_by("date", "start_hour")[:5]
    return render(request, "staff/schedule.html", {
        "cfg": cfg, "day": day, "today": today, "rows": rows, "hours": hours, "ncols": len(hours),
        "now_pct": now_pct, "now_label": local_now.strftime("%I:%M").lstrip("0"),
        "selected": selected, "sel_flag": sel_flag, "no_show_allowed": no_show_allowed,
        "to_collect": to_collect, "sport": sport, "sports": Sport.choices,
        "prev_day": day - timedelta(days=1), "next_day": day + timedelta(days=1),
        "stats": {
            "bookings": len(day_res),
            "walkins": sum(1 for r in day_res if r.source == Reservation.Source.WALKIN),
            "collected": services.todays_collections(today),
        },
        "desk_methods": [(m.value, m.label) for m in DESK_METHODS],
        "minutes_late": int((now - selected.start_dt).total_seconds() // 60) if sel_flag else 0,
    })


@staff_required
@require_POST
def reservation_action(request, code):
    res = get_object_or_404(Reservation.objects.select_related("player", "court"), code=code)
    cfg = SiteSettings.get()
    action = request.POST.get("action")
    nxt = request.POST.get("next", "")
    back = nxt if nxt.startswith("/staff") else f"{reverse('staff_schedule')}?date={res.date}&sel={res.code}"
    user = request.user

    if action == "collect":
        method = request.POST.get("method")
        if method not in DESK_METHODS:
            messages.error(request, "Choose how the player paid.")
        elif res.balance_due <= 0:
            messages.info(request, "Nothing to collect. This booking is fully paid.")
        elif res.status not in (S.PENDING, S.CONFIRMED, S.CHECKED_IN):
            messages.error(request, "This booking can't be paid anymore.")
        else:
            amount = res.balance_due
            services.record_payment(res, method, amount, actor=user,
                                    reference=request.POST.get("reference", "").strip()[:60])
            services.log(user, f"Collected ₱{amount:,.2f} ({PaymentMethod(method).label}) from {res.player.name}", res.code)
            services.send_sms(res.player.mobile, f"Payment of ₱{amount:,.2f} received for {res.code}. You're confirmed.", "info")
            messages.success(request, f"Collected ₱{amount:,.2f}. Booking confirmed.")
    elif action == "check_in":
        if res.status != S.CONFIRMED:
            messages.error(request, "Only confirmed bookings can be checked in.")
        elif res.balance_due > 0:
            messages.error(request, f"Collect the ₱{res.balance_due:,.2f} balance first.")
        else:
            services.check_in(res, user)
            messages.success(request, f"{res.player.name} is checked in.")
    elif action == "no_show":
        if res.status != S.CONFIRMED or timezone.now() < res.start_dt + timedelta(minutes=cfg.grace_minutes):
            messages.error(request, f"You can mark a no-show only {cfg.grace_minutes} minutes after the start time.")
        else:
            services.mark_no_show(res, user)
            messages.success(request, f"Marked as no-show. {res.court.name} is open again.")
    elif action == "cancel_complex":
        if res.status not in Reservation.BLOCKING or res.status == S.COMPLETED:
            messages.error(request, "This booking can't be cancelled.")
        else:
            reason = request.POST.get("reason", "").strip() or "Cancelled by the complex"
            refunded = services.cancel_by_complex(res, user, reason)
            services.log(user, f"Cancelled by complex ({reason}), refunded ₱{refunded:,.2f}", res.code)
            messages.success(request, f"Booking cancelled. Full refund of ₱{refunded:,.2f} recorded.")
    elif action == "text_player":
        services.send_sms(res.player.mobile, f"Hi {res.player.name}, your {services.hour_label(res.start_hour)} "
                                             f"booking at {res.court.name} has started. Are you on your way?", "info")
        services.log(user, f"Texted {res.player.name} about late arrival", res.code)
        messages.success(request, "Text sent.")
    else:
        messages.error(request, "Unknown action.")
    return redirect(back)


# ---------------------------------------------------------------------------
# Reservations & refunds
# ---------------------------------------------------------------------------
STATUS_FILTERS = [
    ("all", "All", None),
    ("pending", "Pending payment", [S.PENDING]),
    ("cancel_requested", "Cancel requests", [S.CANCEL_REQUESTED]),
    ("confirmed", "Confirmed", [S.CONFIRMED, S.CHECKED_IN]),
    ("no_show", "No-shows", [S.NO_SHOW]),
    ("cancelled", "Cancelled and refunded", [S.CANCELLED, S.RELEASED]),
    ("completed", "Completed", [S.COMPLETED]),
]


@staff_required
def reservations(request):
    status = request.GET.get("status", "all")
    q = request.GET.get("q", "").strip()
    qs = Reservation.objects.select_related("player", "court").order_by("-created_at")
    filt = dict((k, v) for k, _l, v in STATUS_FILTERS).get(status)
    if filt:
        qs = qs.filter(status__in=filt)
    if q:
        qs = qs.filter(
            Q(code__icontains=q) | Q(player__name__icontains=q) | Q(player__mobile__icontains=q)
            | Q(payments__reference__icontains=q) | Q(league__name__icontains=q)
        ).distinct()
    page = Paginator(qs, 15).get_page(request.GET.get("page"))
    counts = {
        "pending": Reservation.objects.filter(status=S.PENDING).count(),
        "cancel_requested": Reservation.objects.filter(status=S.CANCEL_REQUESTED).count(),
    }
    refunds = Refund.objects.filter(status=Refund.Status.REQUESTED).select_related(
        "reservation__player", "reservation__court")
    recent_complex = Refund.objects.filter(tier=Refund.Tier.COMPLEX).select_related(
        "reservation__player", "reservation__court").first()
    return render(request, "staff/reservations.html", {
        "page": page, "status": status, "q": q, "filters": STATUS_FILTERS, "counts": counts,
        "refunds": refunds, "recent_complex": recent_complex,
    })


@manager_required
@require_POST
def refund_decide(request, pk):
    refund = get_object_or_404(Refund.objects.select_related("reservation__player"), pk=pk,
                               status=Refund.Status.REQUESTED)
    approve = request.POST.get("decision") == "approve"
    reason = request.POST.get("reason", "").strip()[:200]
    if not approve and not reason:
        messages.error(request, "Add a short reason so the player knows why it was declined.")
    else:
        services.decide_refund(refund, approve, request.user, reason)
        messages.success(request, "Refund approved." if approve else "Refund declined.")
    return redirect(request.POST.get("next") or reverse("staff_reservations"))


# ---------------------------------------------------------------------------
# Walk-in & sales
# ---------------------------------------------------------------------------
@staff_required
def walkin(request):
    cfg = SiteSettings.get()
    today = timezone.localdate()
    day = parse_date(request.GET.get("date") or request.POST.get("date") or "") or today
    courts = list(Court.objects.filter(is_active=True))
    items = list(Item.objects.filter(is_active=True))
    plans = list(MembershipPlan.objects.filter(is_active=True))
    form = {
        "mode": "booking", "court": str(courts[0].pk) if courts else "", "start": "", "end": "",
        "player_type": PlayerType.WALKIN, "name": "", "mobile": "", "method": PaymentMethod.CASH,
        "cash": "", "reference": "", "plan": "", "use_package": False, "qty": {},
    }
    errors = []
    first_hour = cfg.open_hour
    if day == today:
        first_hour = max(cfg.open_hour, min(timezone.localtime().hour, cfg.close_hour - 1))
    form["start"], form["end"] = str(first_hour), str(first_hour + 1)

    if request.method == "POST":
        form.update({
            "mode": request.POST.get("mode") if request.POST.get("mode") in ("booking", "items") else "booking",
            "court": request.POST.get("court", ""),
            "start": request.POST.get("start", ""), "end": request.POST.get("end", ""),
            "player_type": request.POST.get("player_type") if request.POST.get("player_type") in PlayerType.values else PlayerType.WALKIN,
            "name": request.POST.get("name", "").strip()[:120],
            "mobile": request.POST.get("mobile", "").strip(),
            "method": request.POST.get("method") if request.POST.get("method") in DESK_METHODS else PaymentMethod.CASH,
            "cash": request.POST.get("cash", ""), "reference": request.POST.get("reference", "").strip()[:60],
            "plan": request.POST.get("plan", ""), "use_package": request.POST.get("use_package") == "on",
        })
        for i in items:
            form["qty"][i.pk] = max(0, min(50, _int(request.POST.get(f"item_{i.pk}"), 0)))

        mobile = services.normalize_mobile(form["mobile"]) if form["mobile"] else None
        if form["mobile"] and not mobile:
            errors.append("Enter a valid mobile number, or leave it blank.")
        plan = next((p for p in plans if str(p.pk) == form["plan"]), None)
        if plan and not mobile:
            errors.append("Add a mobile number to sell a membership or package.")

        court = start = end = None
        court_quote = None
        package = None
        player = None
        if mobile:
            player = Player.objects.filter(mobile=mobile).first()

        if form["mode"] == "booking":
            court = next((c for c in courts if str(c.pk) == form["court"]), None)
            start, end = _int(form["start"]), _int(form["end"])
            if not court or start is None or end is None or end <= start:
                errors.append("Choose a court and a start and end time.")
            else:
                ok, reason = services.check_range(court, day, start, end, allow_past=(day == today))
                if not ok:
                    errors.append(reason)
                elif form["player_type"] == PlayerType.MEMBER and not (player and player.is_active_member):
                    errors.append("No active membership found for this mobile number.")
                else:
                    court_quote = services.quote(court, day, start, end, form["player_type"])
                    if form["use_package"]:
                        package = None
                        if player:
                            package = player.packages.filter(
                                hours_left__gte=end - start, expires_on__gte=today
                            ).filter(Q(plan__sport="") | Q(plan__sport=court.sport)).first()
                        if not package:
                            errors.append("No package with enough hours for this sport was found for this number.")

        item_lines = [(i, form["qty"][i.pk]) for i in items if form["qty"][i.pk]]
        court_amount = ZERO if package else (court_quote["total"] if court_quote else ZERO)
        items_total = sum((i.price * q for i, q in item_lines), ZERO)
        plan_total = plan.price if plan else ZERO
        grand_total = court_amount + items_total + plan_total
        cash_received = _dec(form["cash"]) if form["method"] == PaymentMethod.CASH else None

        if form["mode"] == "items" and not item_lines and not plan:
            errors.append("Add at least one item.")
        if form["method"] == PaymentMethod.CASH and grand_total > 0:
            if cash_received is None or cash_received < grand_total:
                errors.append(f"Cash received must be at least ₱{grand_total:,.2f}.")

        if not errors:
            user = request.user
            with transaction.atomic():
                if mobile or form["mode"] == "booking" or plan:
                    player = services.get_or_create_player(
                        mobile, form["name"] or "Walk-in", form["player_type"])
                res = None
                if form["mode"] == "booking":
                    hrs = end - start
                    res = Reservation(
                        player=player, court=court, date=day, start_hour=start, end_hour=end,
                        player_type=form["player_type"], source=Reservation.Source.WALKIN,
                        status=S.CONFIRMED, created_by=user,
                        court_fee=court_quote["court_fee"],
                        discount=court_quote["court_fee"] if package else court_quote["discount"],
                        discount_note=f"Package hours ({package.plan.name})" if package else court_quote["discount_note"],
                        total=court_amount, amount_paid=court_amount,
                        payment_method=PaymentMethod.PACKAGE if package else form["method"],
                    )
                    local_now = timezone.localtime()
                    if day == today and start <= local_now.hour < end:
                        res.status = S.CHECKED_IN
                        res.checked_in_at = timezone.now()
                    res.save()
                    if package:
                        package.hours_left -= hrs
                        package.save(update_fields=["hours_left"])
                    if court_amount > 0:
                        Payment.objects.create(reservation=res, method=form["method"], amount=court_amount,
                                               reference=form["reference"], received_by=user)

                sale = Sale.objects.create(
                    customer_name=form["name"] or (player.name if player else "Walk-in"),
                    player=player, reservation=res, method=form["method"],
                    cash_received=cash_received, created_by=user, total=grand_total,
                )
                if res:
                    SaleLine.objects.create(
                        sale=sale, kind=SaleLine.Kind.COURT, quantity=1, unit_price=court_amount,
                        description=f"{court.name} · {services.range_label(start, end)}"
                                    + (" (package hours)" if package else ""),
                    )
                for i, q in item_lines:
                    SaleLine.objects.create(sale=sale, kind=SaleLine.Kind.ITEM, item=i, quantity=q,
                                            unit_price=i.price, description=i.name)
                if plan:
                    SaleLine.objects.create(sale=sale, kind=SaleLine.Kind.PLAN, quantity=1,
                                            unit_price=plan.price, description=plan.name)
                    if plan.kind == MembershipPlan.Kind.MEMBERSHIP:
                        base = max(player.member_until or today, today)
                        player.member_until = base + timedelta(days=plan.valid_days)
                        player.player_type = PlayerType.MEMBER
                        player.save(update_fields=["member_until", "player_type"])
                    else:
                        PlayerPackage.objects.create(player=player, plan=plan, hours_left=plan.hours,
                                                     expires_on=today + timedelta(days=plan.valid_days))
                extra = items_total + plan_total
                if extra > 0:
                    Payment.objects.create(sale=sale, method=form["method"], amount=extra,
                                           reference=form["reference"], received_by=user)
                if player and player.mobile and grand_total > 0:
                    pts = pricing.points_for(grand_total, cfg.peso_per_point)
                    player.loyalty_points += pts
                    player.save(update_fields=["loyalty_points"])
                    if res:
                        res.points_awarded = True
                        res.save(update_fields=["points_awarded"])
                    services.send_sms(player.mobile, f"Thanks for playing! Receipt {sale.code}: ₱{grand_total:,.2f}. "
                                                     f"You earned {pts} loyalty point(s).", "info")
                what = "Walk-in booking and sale" if res else "Walk-in sale"
                services.log(user, f"{what} ₱{grand_total:,.2f} ({PaymentMethod(form['method']).label})", sale.code)
            messages.success(request, f"Charged ₱{grand_total:,.2f}.")
            return redirect("staff_receipt", code=sale.code)

    # Data for the live total and slot strip (JavaScript).
    states = {str(c.pk): services.slot_states(c, day) for c in courts}
    if day == today:
        local_now = timezone.localtime()
        for c in courts:
            st = states[str(c.pk)]
            if st.get(local_now.hour) == "past":
                taken = Reservation.objects.filter(court=c, date=day, status__in=Reservation.BLOCKING,
                                                   start_hour__lte=local_now.hour, end_hour__gt=local_now.hour).exists()
                if not taken:
                    st[local_now.hour] = "open"
    js = {
        "courts": {str(c.pk): {"name": c.name, "sport": c.sport, "day": str(c.day_rate),
                               "evening": str(c.evening_rate)} for c in courts},
        "states": states,
        "promos": [{"pct": p.percent, "days": p.weekday_list, "start": p.start_hour, "end": p.end_hour,
                    "sport": p.sport} for p in Promo.objects.filter(is_active=True)],
        "weekday": day.weekday(),
        "eveningStart": cfg.evening_start_hour,
        "typePct": {v: cfg.discount_for(v) for v in PlayerType.values},
        "items": {str(i.pk): str(i.price) for i in items},
        "plans": {str(p.pk): str(p.price) for p in plans},
        "labels": {h: services.hour_label(h) for h in range(cfg.open_hour, cfg.close_hour + 1)},
    }
    for i in items:
        i.qty = form["qty"].get(i.pk, 0)
    return render(request, "staff/walkin.html", {
        "cfg": cfg, "day": day, "today": today, "courts": courts, "items": items, "plans": plans,
        "form": form, "errors": errors, "sports": Sport.choices,
        "player_types": PlayerType.choices, "desk_methods": [(m.value, m.label) for m in DESK_METHODS],
        "hours": cfg.hours, "end_hours": list(range(cfg.open_hour + 1, cfg.close_hour + 1)),
        "js": js,
    })


@staff_required
def receipt(request, code):
    sale = get_object_or_404(Sale.objects.select_related("player", "reservation", "created_by"), code=code)
    return render(request, "staff/receipt.html", {"sale": sale, "lines": sale.lines.all()})


# ---------------------------------------------------------------------------
# League / tournament block booking
# ---------------------------------------------------------------------------
@staff_required
def league(request):
    cfg = SiteSettings.get()
    courts = list(Court.objects.filter(is_active=True))
    today = timezone.localdate()
    form = {"name": "", "captain": "", "mobile": "", "court": "", "first_date": "", "start": "",
            "end": "", "weeks": "8", "pay_now": False, "method": PaymentMethod.CASH}
    errors, preview = [], None

    if request.method == "POST":
        for k in ("name", "captain", "mobile", "court", "first_date", "start", "end", "weeks", "method"):
            form[k] = request.POST.get(k, "").strip()
        form["pay_now"] = request.POST.get("pay_now") == "on"
        mobile = services.normalize_mobile(form["mobile"])
        court = next((c for c in courts if str(c.pk) == form["court"]), None)
        first = parse_date(form["first_date"])
        start, end, weeks = _int(form["start"]), _int(form["end"]), _int(form["weeks"])
        if not form["name"]:
            errors.append("Enter the league or tournament name.")
        if not form["captain"] or not mobile:
            errors.append("Enter the team captain's name and a valid mobile number.")
        if not court or not first or start is None or end is None or end <= start:
            errors.append("Choose a court, a first date, and a start and end time.")
        elif first < today:
            errors.append("The first date can't be in the past.")
        if not weeks or not 1 <= weeks <= 20:
            errors.append("Number of weeks must be from 1 to 20.")
        if form["pay_now"] and form["method"] not in DESK_METHODS:
            errors.append("Choose a payment method.")

        if not errors:
            rows, total = [], ZERO
            for i in range(weeks):
                d = first + timedelta(days=7 * i)
                ok, reason = services.check_range(court, d, start, end)
                amount = services.quote(court, d, start, end, PlayerType.TEAM)["total"] if ok else ZERO
                total += amount
                rows.append({"date": d, "ok": ok, "reason": reason, "amount": amount})
            free = [r for r in rows if r["ok"]]
            preview = {"rows": rows, "total": total, "free_count": len(free),
                       "conflicts": len(rows) - len(free), "court": court,
                       "time": services.range_label(start, end)}

            if request.POST.get("action") == "create":
                if not free:
                    errors.append("Every date has a conflict. Change the court or time.")
                else:
                    user = request.user
                    with transaction.atomic():
                        player = services.get_or_create_player(mobile, form["captain"], PlayerType.TEAM)
                        lb = LeagueBooking.objects.create(name=form["name"], player=player, court=court,
                                                          start_hour=start, end_hour=end, created_by=user)
                        sale = None
                        if form["pay_now"]:
                            sale = Sale.objects.create(customer_name=form["name"], player=player,
                                                       method=form["method"], created_by=user, total=total)
                        for r in free:
                            q = services.quote(court, r["date"], start, end, PlayerType.TEAM)
                            res = Reservation.objects.create(
                                player=player, court=court, date=r["date"], start_hour=start, end_hour=end,
                                player_type=PlayerType.TEAM, players_count=10, source=Reservation.Source.LEAGUE,
                                league=lb, created_by=user, court_fee=q["court_fee"], discount=q["discount"],
                                discount_note=q["discount_note"], total=q["total"],
                                status=S.CONFIRMED if form["pay_now"] else S.PENDING,
                                payment_method=form["method"] if form["pay_now"] else PaymentMethod.DESK,
                                amount_paid=q["total"] if form["pay_now"] else ZERO,
                            )
                            if form["pay_now"]:
                                Payment.objects.create(reservation=res, method=form["method"], amount=q["total"],
                                                       received_by=user)
                                SaleLine.objects.create(sale=sale, kind=SaleLine.Kind.COURT, quantity=1,
                                                        unit_price=q["total"],
                                                        description=f"{court.name} · {r['date']:%b %d} · {preview['time']}")
                            else:
                                res.payment_deadline = services.payment_deadline_for(res.start_dt)
                                res.save(update_fields=["payment_deadline"])
                        services.log(user, f"Created {form['name']}: {len(free)} date(s) on {court.name}, "
                                           f"total ₱{total:,.2f}", lb.name[:60])
                    services.send_sms(mobile, f"{form['name']} is booked: {len(free)} date(s) at {court.name}, "
                                              f"{preview['time']}. Total ₱{total:,.2f}.", "confirmation")
                    messages.success(request, f"Booked {len(free)} date(s) for {form['name']}.")
                    if sale:
                        return redirect("staff_receipt", code=sale.code)
                    return redirect(f"{reverse('staff_reservations')}?q={form['name']}")

    return render(request, "staff/league.html", {
        "cfg": cfg, "courts": courts, "form": form, "errors": errors, "preview": preview,
        "hours": cfg.hours, "end_hours": list(range(cfg.open_hour + 1, cfg.close_hour + 1)),
        "desk_methods": [(m.value, m.label) for m in DESK_METHODS], "today": today,
    })


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
def _period(request):
    today = timezone.localdate()
    period = request.GET.get("period", "month")
    if period == "today":
        return today, today, "Today", period
    if period == "week":
        return today - timedelta(days=6), today, "Last 7 days", period
    if period == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, end.strftime("%B %Y"), period
    if period == "custom":
        start = parse_date(request.GET.get("from") or "") or today.replace(day=1)
        end = parse_date(request.GET.get("to") or "") or today
        if end < start:
            start, end = end, start
        return start, end, f"{start:%b %d} to {end:%b %d, %Y}", period
    return today.replace(day=1), today, today.strftime("%B %Y"), "month"


def _report_data(start, end):
    cfg = SiteSettings.get()
    courts = list(Court.objects.filter(is_active=True))
    all_res = list(Reservation.objects.filter(date__gte=start, date__lte=end).select_related("court"))
    played = [r for r in all_res if r.status in (S.CONFIRMED, S.CHECKED_IN, S.COMPLETED, S.NO_SHOW)]
    t0 = timezone.make_aware(datetime.combine(start, time.min))
    t1 = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min))
    lines = SaleLine.objects.filter(sale__created_at__gte=t0, sale__created_at__lt=t1)

    court_rev = equipment_rev = ZERO
    for r in all_res:
        if r.amount_paid > 0:
            equip = min(r.equipment_total, r.amount_paid)
            equipment_rev += equip
            court_rev += r.amount_paid - equip
    item_rev = sum((l.line_total for l in lines if l.kind == SaleLine.Kind.ITEM), ZERO)
    plan_rev = sum((l.line_total for l in lines if l.kind == SaleLine.Kind.PLAN), ZERO)
    revenue = court_rev + equipment_rev + item_rev + plan_rev
    sources = [("Court fees", court_rev), ("Equipment add-ons (online)", equipment_rev),
               ("Item sales (drinks, rentals, lockers)", item_rev), ("Memberships and packages", plan_rev)]
    source_rows = [{"label": l, "amount": a, "pct": int(a / revenue * 100) if revenue else 0} for l, a in sources]

    days = (end - start).days + 1
    open_hours = cfg.close_hour - cfg.open_hour
    capacity = len(courts) * days * open_hours
    booked_hours = sum(r.hours for r in played)
    utilization = round(booked_hours / capacity * 100) if capacity else 0

    by_sport = defaultdict(int)
    for r in played:
        by_sport[r.court.get_sport_display()] += 1
    max_sport = max(by_sport.values()) if by_sport else 1
    sport_rows = [{"label": k, "count": v, "pct": int(v / max_sport * 100)}
                  for k, v in sorted(by_sport.items(), key=lambda kv: -kv[1])]

    # Peak hours heatmap: weekday x 2-hour block, % of court-hours booked.
    block_starts = list(range(cfg.open_hour, cfg.close_hour, 2))
    day_counts = defaultdict(int)
    for i in range(days):
        day_counts[(start + timedelta(days=i)).weekday()] += 1
    booked = defaultdict(int)
    for r in played:
        wd = r.date.weekday()
        for h in range(r.start_hour, r.end_hour):
            b = cfg.open_hour + ((h - cfg.open_hour) // 2) * 2
            booked[(wd, b)] += 1
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    heat_rows, cells = [], []
    for wd in range(7):
        row = []
        for b in block_starts:
            width = min(2, cfg.close_hour - b)
            cap = len(courts) * day_counts[wd] * width
            pct = round(booked[(wd, b)] / cap * 100) if cap else 0
            level = 0 if pct < 25 else 1 if pct < 45 else 2 if pct < 65 else 3 if pct < 85 else 4
            row.append({"pct": pct, "level": level})
            if cap:
                cells.append((pct, wd, b))
        heat_rows.append({"name": names[wd], "cells": row})
    block_labels = [f"{services.hour_label(b).replace(':00', '')}" for b in block_starts]
    insight = ""
    if cells and booked_hours:
        busiest = max(cells)
        quietest = min(c for c in cells if c[1] < 5) if any(c[1] < 5 for c in cells) else min(cells)
        insight = (f"Busiest: {names[busiest[1]]} {services.range_label(busiest[2], busiest[2] + 2)} "
                   f"({busiest[0]}% booked). Quietest weekday block: {names[quietest[1]]} "
                   f"{services.range_label(quietest[2], quietest[2] + 2)} ({quietest[0]}%). "
                   f"Off-peak promos work best in the quiet blocks.")

    refunds = Refund.objects.filter(status=Refund.Status.APPROVED, decided_at__gte=t0, decided_at__lt=t1)
    no_shows = sum(1 for r in all_res if r.status == S.NO_SHOW)
    cancel_rows = [
        ("Cancellations", sum(1 for r in all_res if r.status in (S.CANCELLED, S.CANCEL_REQUESTED))),
        ("Full refunds (24 hrs or more)", refunds.filter(tier=Refund.Tier.FULL).count()),
        ("50% refunds (late)", refunds.filter(tier=Refund.Tier.HALF).count()),
        ("Cancelled by the complex", refunds.filter(tier=Refund.Tier.COMPLEX).count()),
        ("Refunds issued", f"₱{sum((x.amount for x in refunds), ZERO):,.2f}"),
        ("Unpaid, auto-released", sum(1 for r in all_res if r.status == S.RELEASED)),
        ("Loyalty free hours redeemed", sum(1 for r in all_res if r.points_redeemed)),
    ]
    return {
        "revenue": revenue, "source_rows": source_rows, "bookings": len(played),
        "online_pct": round(sum(1 for r in played if r.source == Reservation.Source.ONLINE) / len(played) * 100) if played else 0,
        "utilization": utilization, "no_shows": no_shows,
        "no_show_rate": round(no_shows / len(played) * 100, 1) if played else 0,
        "sport_rows": sport_rows, "heat_rows": heat_rows, "block_labels": block_labels,
        "insight": insight, "cancel_rows": cancel_rows, "all_res": all_res,
    }


@manager_required
def reports(request):
    start, end, label, period = _period(request)
    data = _report_data(start, end)
    return render(request, "staff/reports.html", {
        **data, "start": start, "end": end, "label": label, "period": period,
        "periods": [("today", "Today"), ("week", "Last 7 days"), ("month", "This month"),
                    ("last_month", "Last month"), ("custom", "Custom")],
        "query": request.GET.urlencode(),
    })


@manager_required
def reports_export(request):
    start, end, label, _period_code = _period(request)
    data = _report_data(start, end)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="manggahan-report-{start}-to-{end}.csv"'
    response.write("\ufeff")  # so Excel reads the peso sign correctly
    w = csv.writer(response)
    w.writerow(["Manggahan Sports Complex report", label])
    w.writerow([])
    w.writerow(["Total revenue", f"{data['revenue']:.2f}"])
    for row in data["source_rows"]:
        w.writerow([row["label"], f"{row['amount']:.2f}"])
    w.writerow(["Bookings", data["bookings"]])
    w.writerow(["Court utilization %", data["utilization"]])
    w.writerow(["No-show rate %", data["no_show_rate"]])
    w.writerow([])
    w.writerow(["Reservation", "Date", "Start", "End", "Court", "Sport", "Player type", "Source",
                "Status", "Total", "Amount paid"])
    for r in sorted(data["all_res"], key=lambda x: (x.date, x.start_hour)):
        w.writerow([r.code, r.date, services.hour_label(r.start_hour), services.hour_label(r.end_hour),
                    r.court.name, r.court.get_sport_display(), r.get_player_type_display(),
                    r.get_source_display(), r.get_status_display(), f"{r.total:.2f}", f"{r.amount_paid:.2f}"])
    services.log(request.user, f"Exported report ({label})")
    return response


# ---------------------------------------------------------------------------
# Courts, rates & promos
# ---------------------------------------------------------------------------
@manager_required
def courts_rates(request):
    cfg = SiteSettings.get()
    courts = list(Court.objects.all())
    user = request.user
    block_preview = None
    block_form = {"court": "", "date": "", "start": "", "end": "", "reason": ""}

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_rates":
            changed = 0
            for c in courts:
                day_rate = _dec(request.POST.get(f"day_{c.pk}"))
                eve_rate = _dec(request.POST.get(f"eve_{c.pk}"))
                active = request.POST.get(f"active_{c.pk}") == "on"
                if day_rate is None or eve_rate is None or day_rate < 0 or eve_rate < 0:
                    messages.error(request, f"Check the rates for {c.name}.")
                    continue
                if (day_rate, eve_rate, active) != (c.day_rate, c.evening_rate, c.is_active):
                    c.day_rate, c.evening_rate, c.is_active = day_rate, eve_rate, active
                    c.save()
                    changed += 1
            services.log(user, f"Updated rates for {changed} court(s)", "Rates")
            messages.success(request, "Rates saved. They apply to new bookings.")
        elif action == "add_court":
            name = request.POST.get("name", "").strip()
            sport = request.POST.get("sport")
            day_rate, eve_rate = _dec(request.POST.get("day_rate")), _dec(request.POST.get("evening_rate"))
            if name and sport in Sport.values and day_rate is not None and eve_rate is not None:
                Court.objects.create(name=name, sport=sport, day_rate=day_rate, evening_rate=eve_rate,
                                     sort_order=len(courts) + 1)
                services.log(user, f"Added court {name}", "Courts")
                messages.success(request, f"{name} added.")
            else:
                messages.error(request, "Enter a name, sport, and both rates.")
        elif action == "save_discounts":
            student = _int(request.POST.get("student_discount"))
            member = _int(request.POST.get("member_discount"))
            if student is None or member is None or not (0 <= student <= 100 and 0 <= member <= 100):
                messages.error(request, "Discounts must be from 0 to 100.")
            else:
                cfg.student_discount, cfg.member_discount = student, member
                cfg.save()
                services.log(user, f"Set discounts: student {student}%, member {member}%", "Rates")
                messages.success(request, "Discounts saved.")
        elif action == "save_rules":
            fields = ["grace_minutes", "pay_deadline_hours", "pay_cutoff_hours_before", "peso_per_point",
                      "points_per_free_hour", "evening_start_hour"]
            values = {f: _int(request.POST.get(f)) for f in fields}
            if any(v is None or v < 0 for v in values.values()) or not values["peso_per_point"]:
                messages.error(request, "Check the booking rules. All values must be whole numbers.")
            else:
                for f, v in values.items():
                    setattr(cfg, f, v)
                cfg.save()
                services.log(user, "Updated booking and loyalty rules", "Rules")
                messages.success(request, "Rules saved.")
        elif action == "add_promo":
            name = request.POST.get("name", "").strip()
            pct = _int(request.POST.get("percent"))
            days = [d for d in request.POST.getlist("weekdays") if d.isdigit()]
            start, end = _int(request.POST.get("start_hour")), _int(request.POST.get("end_hour"))
            sport = request.POST.get("sport", "")
            if not name or not pct or not 0 < pct <= 100 or not days or start is None or end is None or end <= start:
                messages.error(request, "Fill in the promo name, percent, days, and hours.")
            else:
                Promo.objects.create(name=name, percent=pct, weekdays=",".join(days), start_hour=start,
                                     end_hour=end, sport=sport if sport in Sport.values else "")
                services.log(user, f"Added promo {name} ({pct}% off)", "Promos")
                messages.success(request, "Promo added.")
        elif action in ("toggle_promo", "delete_promo"):
            promo = get_object_or_404(Promo, pk=request.POST.get("pk"))
            if action == "toggle_promo":
                promo.is_active = not promo.is_active
                promo.save(update_fields=["is_active"])
                services.log(user, f"{'Turned on' if promo.is_active else 'Turned off'} promo {promo.name}", "Promos")
            else:
                services.log(user, f"Deleted promo {promo.name}", "Promos")
                promo.delete()
            messages.success(request, "Promo updated.")
        elif action == "add_plan":
            name = request.POST.get("name", "").strip()
            kind = request.POST.get("kind")
            price = _dec(request.POST.get("price"))
            if not name or kind not in MembershipPlan.Kind.values or price is None:
                messages.error(request, "Enter a name, type, and price.")
            else:
                MembershipPlan.objects.create(
                    name=name, kind=kind, price=price, hours=_int(request.POST.get("hours"), 0) or 0,
                    sport=request.POST.get("sport") if request.POST.get("sport") in Sport.values else "",
                    valid_days=_int(request.POST.get("valid_days"), 30) or 30,
                    description=request.POST.get("description", "").strip()[:160],
                )
                services.log(user, f"Added plan {name}", "Plans")
                messages.success(request, "Plan added.")
        elif action == "toggle_plan":
            plan = get_object_or_404(MembershipPlan, pk=request.POST.get("pk"))
            plan.is_active = not plan.is_active
            plan.save(update_fields=["is_active"])
            messages.success(request, "Plan updated.")
        elif action == "save_items":
            for item in Item.objects.all():
                price = _dec(request.POST.get(f"price_{item.pk}"))
                active = request.POST.get(f"item_active_{item.pk}") == "on"
                if price is not None and (price != item.price or active != item.is_active):
                    item.price, item.is_active = price, active
                    item.save(update_fields=["price", "is_active"])
            services.log(user, "Updated item prices", "Items")
            messages.success(request, "Item prices saved.")
        elif action == "add_item":
            name = request.POST.get("name", "").strip()
            cat = request.POST.get("category")
            price = _dec(request.POST.get("price"))
            if name and cat in Item.Category.values and price is not None:
                Item.objects.create(name=name, category=cat, price=price,
                                    bookable_online=request.POST.get("bookable_online") == "on")
                messages.success(request, f"{name} added.")
            else:
                messages.error(request, "Enter the item name, category, and price.")
        elif action in ("block_preview", "block_confirm"):
            for k in block_form:
                block_form[k] = request.POST.get(k, "").strip()
            court = next((c for c in courts if str(c.pk) == block_form["court"]), None)
            d = parse_date(block_form["date"])
            start, end = _int(block_form["start"]), _int(block_form["end"])
            if not court or not d or start is None or end is None or end <= start or not block_form["reason"]:
                messages.error(request, "Choose the court, date, time, and a reason.")
            elif action == "block_preview":
                affected = list(Reservation.objects.filter(
                    court=court, date=d, status__in=Reservation.BLOCKING, start_hour__lt=end, end_hour__gt=start
                ).select_related("player"))
                block_preview = {"court": court, "date": d, "time": services.range_label(start, end),
                                 "affected": affected,
                                 "total": sum((r.amount_paid for r in affected), ZERO)}
            else:
                affected, total = services.block_court(court, d, start, end, block_form["reason"], user)
                messages.success(request, f"{court.name} blocked. {len(affected)} booking(s) cancelled "
                                          f"with full refunds totaling ₱{total:,.2f}.")
                block_form = {"court": "", "date": "", "start": "", "end": "", "reason": ""}
        if block_preview is None:
            return redirect("staff_courts")

    return render(request, "staff/courts.html", {
        "cfg": cfg, "courts": courts, "promos": Promo.objects.all(), "plans": MembershipPlan.objects.all(),
        "items": Item.objects.all(), "sports": Sport.choices, "hours": cfg.hours,
        "end_hours": list(range(cfg.open_hour + 1, cfg.close_hour + 1)),
        "weekday_names": list(enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])),
        "plan_kinds": MembershipPlan.Kind.choices, "item_categories": Item.Category.choices,
        "block_preview": block_preview, "block_form": block_form,
        "upcoming_blocks": MaintenanceBlock.objects.filter(date__gte=timezone.localdate()).select_related("court")[:8],
        "active_members": Player.objects.filter(member_until__gte=timezone.localdate()).count(),
    })


# ---------------------------------------------------------------------------
# Staff & activity
# ---------------------------------------------------------------------------
@manager_required
def team(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_staff":
            username = request.POST.get("username", "").strip()
            full_name = request.POST.get("full_name", "").strip()
            password = request.POST.get("password", "")
            role = request.POST.get("role")
            if not username or not full_name or len(password) < 6 or role not in StaffProfile.Role.values:
                messages.error(request, "Enter a username, full name, role, and a password of at least 6 characters.")
            elif User.objects.filter(username=username).exists():
                messages.error(request, "That username is taken.")
            else:
                first, _, last = full_name.partition(" ")
                u = User.objects.create_user(username=username, password=password, first_name=first, last_name=last)
                StaffProfile.objects.create(user=u, role=role, shift=request.POST.get("shift", "").strip()[:40])
                services.log(request.user, f"Added staff account {full_name} ({role.replace('_', ' ')})", "Staff")
                messages.success(request, f"{full_name} can now sign in as {username}.")
        elif action == "toggle_active":
            u = get_object_or_404(User, pk=request.POST.get("pk"))
            if u == request.user:
                messages.error(request, "You can't deactivate your own account.")
            else:
                u.is_active = not u.is_active
                u.save(update_fields=["is_active"])
                services.log(request.user, f"{'Reactivated' if u.is_active else 'Deactivated'} {u.get_full_name() or u.username}", "Staff")
                messages.success(request, "Account updated.")
        return redirect("staff_team")

    staff = User.objects.filter(Q(staff_profile__isnull=False) | Q(is_superuser=True)).select_related("staff_profile").order_by("first_name")
    logs = ActivityLog.objects.select_related("actor")
    actor = request.GET.get("actor", "")
    if actor == "system":
        logs = logs.filter(actor__isnull=True)
    elif actor.isdigit():
        logs = logs.filter(actor_id=int(actor))
    day = parse_date(request.GET.get("date") or "")
    if day:
        t0 = timezone.make_aware(datetime.combine(day, time.min))
        logs = logs.filter(created_at__gte=t0, created_at__lt=t0 + timedelta(days=1))
    page = Paginator(logs, 25).get_page(request.GET.get("page"))
    roles = [
        ("Book, check in, walk-ins", True, True),
        ("Collect payment, sales", True, True),
        ("Mark no-shows", True, True),
        ("League and tournament booking", True, True),
        ("Approve or decline refunds", False, True),
        ("Edit rates, promos, plans", False, True),
        ("Block courts for maintenance", False, True),
        ("View and export reports", False, True),
        ("Manage staff accounts", False, True),
    ]
    return render(request, "staff/team.html", {
        "staff": staff, "page": page, "actor": actor, "day": day, "roles": roles,
        "sms": SmsMessage.objects.all()[:12], "role_choices": StaffProfile.Role.choices,
    })


@manager_required
def activity_export(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="manggahan-activity-log.csv"'
    response.write("\ufeff")
    w = csv.writer(response)
    w.writerow(["Time", "By", "Action", "Reference"])
    for entry in ActivityLog.objects.select_related("actor")[:5000]:
        w.writerow([timezone.localtime(entry.created_at).strftime("%Y-%m-%d %I:%M %p"),
                    entry.actor_name, entry.action, entry.reference])
    return response
