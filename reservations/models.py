import random
import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------
class Sport(models.TextChoices):
    BASKETBALL = "basketball", "Basketball"
    VOLLEYBALL = "volleyball", "Volleyball"
    BADMINTON = "badminton", "Badminton"
    TENNIS = "tennis", "Tennis"
    TABLE_TENNIS = "table_tennis", "Table tennis"
    PICKLEBALL = "pickleball", "Pickleball"


class PlayerType(models.TextChoices):
    WALKIN = "walkin", "Walk-in / Guest"
    MEMBER = "member", "Member"
    STUDENT = "student", "Student"
    TEAM = "team", "Team / League"
    SENIOR_PWD = "senior_pwd", "Senior citizen / PWD"


class PaymentMethod(models.TextChoices):
    GCASH = "gcash", "GCash"
    MAYA = "maya", "Maya"
    CARD = "card", "Debit / credit card"
    CASH = "cash", "Cash"
    DESK = "desk", "Pay at the front desk"
    PACKAGE = "package", "Package hours"


ONLINE_METHODS = [PaymentMethod.GCASH, PaymentMethod.MAYA, PaymentMethod.CARD]


# ---------------------------------------------------------------------------
# Settings (one row)
# ---------------------------------------------------------------------------
class SiteSettings(models.Model):
    open_hour = models.PositiveSmallIntegerField(default=6)
    close_hour = models.PositiveSmallIntegerField(default=22)
    evening_start_hour = models.PositiveSmallIntegerField(default=18)
    student_discount = models.PositiveSmallIntegerField(default=10)
    member_discount = models.PositiveSmallIntegerField(default=10)
    senior_pwd_discount = models.PositiveSmallIntegerField(
        default=20, help_text="Fixed at 20% by Philippine law."
    )
    grace_minutes = models.PositiveSmallIntegerField(default=15)
    hold_minutes = models.PositiveSmallIntegerField(default=10)
    pay_deadline_hours = models.PositiveSmallIntegerField(default=24)
    pay_cutoff_hours_before = models.PositiveSmallIntegerField(default=3)
    peso_per_point = models.PositiveSmallIntegerField(default=100)
    points_per_free_hour = models.PositiveSmallIntegerField(default=100)
    points_expiry_months = models.PositiveSmallIntegerField(default=12)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self):
        return "Site settings"

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def discount_for(self, player_type):
        return {
            "student": self.student_discount,
            "member": self.member_discount,
            "senior_pwd": self.senior_pwd_discount,
        }.get(str(player_type), 0)

    @property
    def hours(self):
        return list(range(self.open_hour, self.close_hour))


# ---------------------------------------------------------------------------
# Courts, items, promos, plans
# ---------------------------------------------------------------------------
class Court(models.Model):
    name = models.CharField(max_length=60)
    sport = models.CharField(max_length=20, choices=Sport.choices)
    day_rate = models.DecimalField(max_digits=8, decimal_places=2)
    evening_rate = models.DecimalField(max_digits=8, decimal_places=2)
    unit_note = models.CharField(max_length=40, blank=True, help_text='e.g. "per table"')
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Item(models.Model):
    class Category(models.TextChoices):
        RENTAL = "rental", "Equipment rental"
        FOOD = "food", "Food and drinks"
        LOCKER = "locker", "Locker and towel"

    name = models.CharField(max_length=60)
    category = models.CharField(max_length=10, choices=Category.choices)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    sport = models.CharField(max_length=20, choices=Sport.choices, blank=True,
                             help_text="Leave blank if it fits every sport.")
    bookable_online = models.BooleanField(default=False,
                                          help_text="Show as an add-on in online booking.")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["category", "sort_order", "name"]

    def __str__(self):
        return f"{self.name} (₱{self.price})"


class Promo(models.Model):
    name = models.CharField(max_length=80)
    percent = models.PositiveSmallIntegerField()
    weekdays = models.CharField(max_length=20, default="0,1,2,3,4",
                                help_text="Comma-separated, Monday=0 ... Sunday=6")
    start_hour = models.PositiveSmallIntegerField(default=6)
    end_hour = models.PositiveSmallIntegerField(default=12)
    sport = models.CharField(max_length=20, choices=Sport.choices, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.name} ({self.percent}%)"

    @property
    def weekday_list(self):
        return [int(d) for d in self.weekdays.split(",") if d.strip().isdigit()]

    @property
    def weekdays_label(self):
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        days = self.weekday_list
        if days == [0, 1, 2, 3, 4]:
            return "Mon to Fri"
        if days == [5, 6]:
            return "Sat and Sun"
        if days == list(range(7)):
            return "Every day"
        return ", ".join(names[d] for d in days if 0 <= d <= 6)

    def applies(self, day, hour, sport):
        return (
            self.is_active
            and day.weekday() in self.weekday_list
            and self.start_hour <= hour < self.end_hour
            and (not self.sport or self.sport == sport)
        )


class MembershipPlan(models.Model):
    class Kind(models.TextChoices):
        MEMBERSHIP = "membership", "Membership"
        PACKAGE = "package", "Prepaid hour package"

    name = models.CharField(max_length=80)
    kind = models.CharField(max_length=12, choices=Kind.choices)
    price = models.DecimalField(max_digits=8, decimal_places=2)
    hours = models.PositiveSmallIntegerField(default=0, help_text="Hours included (packages only).")
    sport = models.CharField(max_length=20, choices=Sport.choices, blank=True)
    valid_days = models.PositiveSmallIntegerField(default=30)
    description = models.CharField(max_length=160, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------
class StaffProfile(models.Model):
    class Role(models.TextChoices):
        FRONT_DESK = "front_desk", "Front desk"
        MANAGER = "manager", "Manager"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="staff_profile")
    role = models.CharField(max_length=12, choices=Role.choices, default=Role.FRONT_DESK)
    shift = models.CharField(max_length=40, blank=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.get_role_display()})"


class Player(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="player")
    name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=20, unique=True, null=True, blank=True)
    player_type = models.CharField(max_length=12, choices=PlayerType.choices,
                                   default=PlayerType.WALKIN)
    member_until = models.DateField(null=True, blank=True)
    loyalty_points = models.PositiveIntegerField(default=0)
    no_show_count = models.PositiveIntegerField(default=0)
    sms_reminders = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.mobile or 'no mobile'})"

    @property
    def is_active_member(self):
        return bool(self.member_until and self.member_until >= timezone.localdate())

    @property
    def masked_mobile(self):
        if not self.mobile or len(self.mobile) < 7:
            return self.mobile or ""
        return f"{self.mobile[:4]} ••• {self.mobile[-4:]}"


class PlayerPackage(models.Model):
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="packages")
    plan = models.ForeignKey(MembershipPlan, on_delete=models.PROTECT)
    hours_left = models.PositiveSmallIntegerField(default=0)
    expires_on = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.plan.name} for {self.player.name} ({self.hours_left} hrs left)"


class PhoneVerification(models.Model):
    mobile = models.CharField(max_length=20)
    code = models.CharField(max_length=6)
    attempts = models.PositiveSmallIntegerField(default=0)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------
class LeagueBooking(models.Model):
    name = models.CharField(max_length=120)
    player = models.ForeignKey(Player, on_delete=models.PROTECT)
    court = models.ForeignKey(Court, on_delete=models.PROTECT)
    start_hour = models.PositiveSmallIntegerField()
    end_hour = models.PositiveSmallIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending payment"
        CONFIRMED = "confirmed", "Confirmed"
        CHECKED_IN = "checked_in", "Checked in"
        COMPLETED = "completed", "Completed"
        CANCEL_REQUESTED = "cancel_requested", "Cancel requested"
        CANCELLED = "cancelled", "Cancelled"
        NO_SHOW = "no_show", "No-show"
        RELEASED = "released", "Released (unpaid)"

    class Source(models.TextChoices):
        ONLINE = "online", "Online"
        WALKIN = "walkin", "Front desk"
        LEAGUE = "league", "League / tournament"

    # Statuses that occupy a court slot.
    BLOCKING = [Status.PENDING, Status.CONFIRMED, Status.CHECKED_IN, Status.COMPLETED]

    code = models.CharField(max_length=20, unique=True, editable=False)
    manage_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    player = models.ForeignKey(Player, on_delete=models.PROTECT, related_name="reservations")
    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name="reservations")
    date = models.DateField()
    start_hour = models.PositiveSmallIntegerField()
    end_hour = models.PositiveSmallIntegerField()
    player_type = models.CharField(max_length=12, choices=PlayerType.choices)
    players_count = models.PositiveSmallIntegerField(default=1)

    court_fee = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    discount_note = models.CharField(max_length=120, blank=True)
    free_hour_value = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    equipment_total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)

    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, blank=True)
    payment_deadline = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.ONLINE)
    league = models.ForeignKey(LeagueBooking, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="reservations")

    points_redeemed = models.PositiveIntegerField(default=0)
    points_awarded = models.BooleanField(default=False)
    reminder_sent = models.BooleanField(default=False)
    deadline_reminder_sent = models.BooleanField(default=False)

    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-start_hour"]
        indexes = [models.Index(fields=["court", "date"]), models.Index(fields=["status"])]

    def __str__(self):
        return f"{self.code} · {self.court} · {self.date} {self.start_hour}:00"

    def save(self, *args, **kwargs):
        if not self.code:
            year = timezone.localdate().year
            while True:
                candidate = f"MSC-{year}-{random.randint(10000, 99999)}"
                if not Reservation.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
        super().save(*args, **kwargs)

    # --- helpers -----------------------------------------------------------
    @property
    def hours(self):
        return self.end_hour - self.start_hour

    @property
    def hour_list(self):
        return list(range(self.start_hour, self.end_hour))

    @property
    def start_dt(self):
        tz = timezone.get_current_timezone()
        return timezone.make_aware(datetime.combine(self.date, time(self.start_hour)), tz)

    @property
    def end_dt(self):
        tz = timezone.get_current_timezone()
        if self.end_hour >= 24:
            return timezone.make_aware(datetime.combine(self.date, time(23, 59)), tz)
        return timezone.make_aware(datetime.combine(self.date, time(self.end_hour)), tz)

    @property
    def balance_due(self):
        return max(self.total - self.amount_paid, ZERO)

    @property
    def is_fully_paid(self):
        return self.amount_paid >= self.total

    @property
    def is_upcoming(self):
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED) and self.end_dt > timezone.now()

    @property
    def can_cancel(self):
        return self.status in (self.Status.PENDING, self.Status.CONFIRMED) and self.start_dt > timezone.now()

    @property
    def can_reschedule(self):
        return self.can_cancel and self.source != self.Source.LEAGUE

    def possible_no_show(self, now=None, grace_minutes=15):
        now = now or timezone.now()
        return (
            self.status == self.Status.CONFIRMED
            and now >= self.start_dt + timedelta(minutes=grace_minutes)
            and now < self.end_dt
        )


class ReservationItem(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)

    @property
    def line_total(self):
        return self.unit_price * self.quantity


class Refund(models.Model):
    class Tier(models.TextChoices):
        FULL = "full", "24 hrs or more: full refund"
        HALF = "half", "Less than 24 hrs: 50% refund"
        NONE = "none", "After start: no refund"
        COMPLEX = "complex", "Cancelled by the complex: full refund"
        DIFFERENCE = "difference", "Reschedule price difference"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Waiting for approval"
        APPROVED = "approved", "Approved"
        DECLINED = "declined", "Declined"

    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name="refunds")
    tier = models.CharField(max_length=12, choices=Tier.choices)
    percent = models.PositiveSmallIntegerField(default=0)
    amount = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    destination = models.CharField(max_length=40, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    hours_before = models.DecimalField(max_digits=8, decimal_places=1, null=True, blank=True)
    reason = models.CharField(max_length=200, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-requested_at"]


class Payment(models.Model):
    reservation = models.ForeignKey(Reservation, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="payments")
    sale = models.ForeignKey("Sale", null=True, blank=True, on_delete=models.SET_NULL,
                             related_name="payments")
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reference = models.CharField(max_length=60, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MaintenanceBlock(models.Model):
    court = models.ForeignKey(Court, on_delete=models.CASCADE, related_name="blocks")
    date = models.DateField()
    start_hour = models.PositiveSmallIntegerField()
    end_hour = models.PositiveSmallIntegerField()
    reason = models.CharField(max_length=120)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "start_hour"]

    def __str__(self):
        return f"{self.court} {self.date} {self.start_hour}-{self.end_hour}: {self.reason}"


# ---------------------------------------------------------------------------
# Walk-in & sales
# ---------------------------------------------------------------------------
class Sale(models.Model):
    code = models.CharField(max_length=20, unique=True, editable=False)
    customer_name = models.CharField(max_length=120, blank=True)
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="sales")
    reservation = models.ForeignKey(Reservation, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name="sales")
    total = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    method = models.CharField(max_length=10, choices=PaymentMethod.choices)
    cash_received = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            while True:
                candidate = f"R-{random.randint(100000, 999999)}"
                if not Sale.objects.filter(code=candidate).exists():
                    self.code = candidate
                    break
        super().save(*args, **kwargs)

    @property
    def change(self):
        if self.cash_received is None:
            return None
        return max(self.cash_received - self.total, ZERO)


class SaleLine(models.Model):
    class Kind(models.TextChoices):
        COURT = "court", "Court booking"
        ITEM = "item", "Item"
        PLAN = "plan", "Membership or package"

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=6, choices=Kind.choices)
    description = models.CharField(max_length=160)
    item = models.ForeignKey(Item, null=True, blank=True, on_delete=models.SET_NULL)
    quantity = models.PositiveSmallIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    @property
    def line_total(self):
        return self.unit_price * self.quantity


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
class ActivityLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL, related_name="+")
    action = models.CharField(max_length=255)
    reference = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def actor_name(self):
        if not self.actor:
            return "System"
        return self.actor.get_full_name() or self.actor.username


class SmsMessage(models.Model):
    """Outbox for SMS. In demo mode nothing is really sent; messages are stored here."""

    mobile = models.CharField(max_length=20)
    body = models.TextField()
    kind = models.CharField(max_length=20, default="info")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
