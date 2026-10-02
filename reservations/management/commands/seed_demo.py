"""
Fill the database with the complex's courts, rates, items, promos, plans,
staff accounts, a demo player, and sample bookings for the reports.

    python manage.py seed_demo            # safe to run more than once
    python manage.py seed_demo --reset    # wipe bookings first, then reseed
"""
import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reservations import services
from reservations.models import (
    ActivityLog,
    Court,
    Item,
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

COURTS = [
    ("Basketball A", Sport.BASKETBALL, 500, 600, ""),
    ("Basketball B", Sport.BASKETBALL, 500, 600, ""),
    ("Volleyball Court", Sport.VOLLEYBALL, 400, 500, ""),
    ("Badminton Court 1", Sport.BADMINTON, 250, 300, ""),
    ("Badminton Court 2", Sport.BADMINTON, 250, 300, ""),
    ("Badminton Court 3", Sport.BADMINTON, 250, 300, ""),
    ("Tennis Court", Sport.TENNIS, 300, 400, ""),
    ("Table Tennis 1", Sport.TABLE_TENNIS, 100, 150, "per table"),
    ("Table Tennis 2", Sport.TABLE_TENNIS, 100, 150, "per table"),
    ("Table Tennis 3", Sport.TABLE_TENNIS, 100, 150, "per table"),
    ("Table Tennis 4", Sport.TABLE_TENNIS, 100, 150, "per table"),
    ("Pickleball Court", Sport.PICKLEBALL, 300, 400, ""),
]

ITEMS = [
    ("Badminton racket", "rental", 50, Sport.BADMINTON, True),
    ("Shuttlecock", "rental", 40, Sport.BADMINTON, True),
    ("Basketball", "rental", 50, Sport.BASKETBALL, True),
    ("Volleyball", "rental", 50, Sport.VOLLEYBALL, True),
    ("Tennis racket", "rental", 60, Sport.TENNIS, True),
    ("Table tennis paddle", "rental", 40, Sport.TABLE_TENNIS, True),
    ("Pickleball paddle", "rental", 40, Sport.PICKLEBALL, True),
    ("Locker", "locker", 30, "", True),
    ("Towel", "locker", 20, "", False),
    ("Bottled water", "food", 25, "", False),
    ("Sports drink", "food", 45, "", False),
    ("Snack", "food", 35, "", False),
]

PLANS = [
    ("Monthly membership", "membership", 500, 0, "", 30, "10% off bookings, priority booking"),
    ("Badminton 10-hour pack", "package", 2250, 10, Sport.BADMINTON, 180, "Pay for 9 hours, play 10 (daytime)"),
    ("Basketball 10-hour pack", "package", 4500, 10, Sport.BASKETBALL, 180, "Pay for 9 hours, play 10 (daytime)"),
]

NAMES = ["R. Villanueva", "K. Tan", "A. Mendoza", "J. Reyes", "C. Bautista", "E. Ramos", "P. Garcia",
         "M. Santos", "L. Garcia", "D. Aquino", "S. Navarro", "T. Lim", "G. Lopez", "B. Cruz",
         "F. Dela Cruz", "H. Ocampo", "I. Mercado", "N. Castillo", "O. Torres", "V. Pascual"]
TEAMS = ["San Isidro Ballers", "Office League", "Smash Masters", "Shuttle Club", "Brgy. Youth League",
         "Ping Pong Club", "Pickleball Club", "Volleyball Clinic"]


class Command(BaseCommand):
    help = "Load courts, rates, staff accounts, and sample bookings for the demo."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete bookings, sales and logs first.")
        parser.add_argument("--no-bookings", action="store_true", help="Only load setup data, no sample bookings.")

    @transaction.atomic
    def handle(self, *args, **opts):
        random.seed(42)
        if opts["reset"]:
            for model in (Refund, Payment, SaleLine, Sale, Reservation, MaintenanceBlock, ActivityLog, SmsMessage, PlayerPackage):
                model.objects.all().delete()
            Player.objects.filter(user__isnull=True).delete()
            self.stdout.write("Cleared bookings, sales, and logs.")

        SiteSettings.get()
        for i, (name, sport, day, eve, note) in enumerate(COURTS):
            Court.objects.update_or_create(name=name, defaults=dict(
                sport=sport, day_rate=day, evening_rate=eve, unit_note=note, sort_order=i))
        for i, (name, cat, price, sport, online) in enumerate(ITEMS):
            Item.objects.update_or_create(name=name, defaults=dict(
                category=cat, price=price, sport=sport, bookable_online=online, sort_order=i))
        Promo.objects.update_or_create(name="Weekday mornings", defaults=dict(
            percent=20, weekdays="0,1,2,3,4", start_hour=6, end_hour=12, sport="", is_active=True))
        Promo.objects.update_or_create(name="Lunch-hour badminton", defaults=dict(
            percent=20, weekdays="0,1,2,3,4", start_hour=12, end_hour=14, sport=Sport.BADMINTON, is_active=False))
        for name, kind, price, hours, sport, days, desc in PLANS:
            MembershipPlan.objects.update_or_create(name=name, defaults=dict(
                kind=kind, price=price, hours=hours, sport=sport, valid_days=days, description=desc))

        User = get_user_model()
        accounts = [
            ("manager", "manager123", "Maria", "Ramirez", StaffProfile.Role.MANAGER, "Day shift"),
            ("frontdesk", "frontdesk123", "Jose", "Santos", StaffProfile.Role.FRONT_DESK, "Day shift"),
            ("frontdesk2", "frontdesk123", "Rina", "Dizon", StaffProfile.Role.FRONT_DESK, "Night shift"),
        ]
        staff_users = {}
        for username, pw, first, last, role, shift in accounts:
            u, created = User.objects.get_or_create(username=username, defaults={"first_name": first, "last_name": last})
            if created:
                u.set_password(pw)
                u.save()
            StaffProfile.objects.update_or_create(user=u, defaults={"role": role, "shift": shift})
            staff_users[username] = u
        if not User.objects.filter(is_superuser=True).exists():
            User.objects.create_superuser("admin", password="admin12345")

        demo_user, created = User.objects.get_or_create(username="09171234567", defaults={"first_name": "Andrea"})
        if created:
            demo_user.set_password("player123")
            demo_user.save()
        demo_player, _ = Player.objects.get_or_create(mobile="09171234567", defaults={"name": "Andrea Cruz"})
        demo_player.user = demo_user
        demo_player.player_type = PlayerType.STUDENT
        demo_player.loyalty_points = max(demo_player.loyalty_points, 125)
        demo_player.save()

        if not opts["no_bookings"] and not Reservation.objects.exists():
            self._sample_bookings(staff_users, demo_player)

        self.stdout.write(self.style.SUCCESS("Demo data ready."))
        self.stdout.write("  Staff (manager):    manager / manager123")
        self.stdout.write("  Staff (front desk): frontdesk / frontdesk123")
        self.stdout.write("  Player account:     0917 123 4567 / player123")
        self.stdout.write("  Database admin:     admin / admin12345  (at /django-admin/)")

    # ------------------------------------------------------------------
    def _sample_bookings(self, staff, demo_player):
        cfg = SiteSettings.get()
        courts = list(Court.objects.all())
        today = timezone.localdate()
        now = timezone.now()
        desk = staff["frontdesk"]
        players = []
        for i, name in enumerate(NAMES):
            mobile = f"0918{1000000 + i * 7919:07d}"
            p, _ = Player.objects.get_or_create(mobile=mobile, defaults={"name": name,
                                                "player_type": random.choice(list(PlayerType.values))})
            players.append(p)
        team_players = []
        for i, name in enumerate(TEAMS):
            p, _ = Player.objects.get_or_create(mobile=f"0927{2000000 + i * 104729:07d}",
                                                defaults={"name": name, "player_type": PlayerType.TEAM})
            team_players.append(p)

        def make(court, day, start, end, player, status, source=Reservation.Source.ONLINE, method=None):
            q = services.quote(court, day, start, end, player.player_type)
            method = method or random.choice([PaymentMethod.GCASH, PaymentMethod.MAYA, PaymentMethod.CASH, PaymentMethod.CARD])
            paid = q["total"] if status in (S.CONFIRMED, S.CHECKED_IN, S.COMPLETED, S.NO_SHOW, S.CANCEL_REQUESTED) else Decimal("0")
            r = Reservation.objects.create(
                player=player, court=court, date=day, start_hour=start, end_hour=end,
                player_type=player.player_type, players_count=random.randint(2, 12),
                court_fee=q["court_fee"], discount=q["discount"], discount_note=q["discount_note"],
                total=q["total"], amount_paid=paid, payment_method=method if paid else PaymentMethod.DESK,
                status=status, source=source, created_by=desk if source != Reservation.Source.ONLINE else None,
                points_awarded=bool(paid),
            )
            if paid:
                p = Payment.objects.create(reservation=r, method=method, amount=paid, received_by=desk)
                created = timezone.make_aware(datetime.combine(min(day, today), time(min(start, 20))))
                Payment.objects.filter(pk=p.pk).update(created_at=min(created, now))
            return r

        # Past 45 days for the reports: evenings and weekends busier, weekday midday quiet.
        for back in range(45, 0, -1):
            day = today - timedelta(days=back)
            weekend = day.weekday() >= 5
            for court in courts:
                h = cfg.open_hour
                while h < cfg.close_hour:
                    evening = h >= cfg.evening_start_hour
                    midday = 10 <= h < 14
                    chance = 0.8 if evening else (0.7 if weekend else (0.18 if midday else 0.35))
                    if random.random() < chance:
                        length = random.choice([1, 2, 2])
                        end = min(h + length, cfg.close_hour)
                        player = random.choice(team_players if random.random() < 0.25 else players)
                        roll = random.random()
                        status = S.COMPLETED if roll > 0.07 else (S.NO_SHOW if roll > 0.04 else S.CANCELLED)
                        source = Reservation.Source.WALKIN if random.random() < 0.4 else Reservation.Source.ONLINE
                        r = make(court, day, h, end, player, status, source)
                        if status == S.CANCELLED:
                            r.amount_paid = Decimal("0")
                            r.save(update_fields=["amount_paid"])
                            tier = random.choice(["full", "full", "half"])
                            pct = 100 if tier == "full" else 50
                            ref = Refund.objects.create(reservation=r, tier=tier, percent=pct,
                                                        amount=services.pricing.refund_amount(r.total, pct),
                                                        status=Refund.Status.APPROVED,
                                                        destination="GCash", decided_by=staff["manager"])
                            Refund.objects.filter(pk=ref.pk).update(
                                decided_at=timezone.make_aware(datetime.combine(day, time(9))))
                        h = end
                    else:
                        h += 1
            # Counter sales for the day
            for _ in range(random.randint(3, 9)):
                sale = Sale.objects.create(customer_name=random.choice(NAMES), method=PaymentMethod.CASH,
                                           created_by=desk, total=0)
                total = Decimal("0")
                for item in random.sample(list(Item.objects.filter(category__in=["food", "rental", "locker"])), 2):
                    qty = random.randint(1, 3)
                    SaleLine.objects.create(sale=sale, kind=SaleLine.Kind.ITEM, item=item, quantity=qty,
                                            unit_price=item.price, description=item.name)
                    total += item.price * qty
                sale.total = total
                sale.save(update_fields=["total"])
                Sale.objects.filter(pk=sale.pk).update(
                    created_at=timezone.make_aware(datetime.combine(day, time(random.randint(7, 21)))))
            if back % 9 == 0:
                plan = MembershipPlan.objects.order_by("?").first()
                sale = Sale.objects.create(customer_name=random.choice(NAMES), method=PaymentMethod.GCASH,
                                           created_by=desk, total=plan.price)
                SaleLine.objects.create(sale=sale, kind=SaleLine.Kind.PLAN, quantity=1, unit_price=plan.price,
                                        description=plan.name)
                Sale.objects.filter(pk=sale.pk).update(created_at=timezone.make_aware(datetime.combine(day, time(10))))

        # Today and the coming days: a realistic mix for the schedule screen.
        local_hour = timezone.localtime().hour
        by_name = {c.name: c for c in courts}
        plan_today = [
            ("Basketball A", 8, 10, team_players[4]), ("Basketball A", 18, 20, team_players[0]),
            ("Basketball B", 15, 17, players[0]), ("Badminton Court 1", 7, 8, players[2]),
            ("Badminton Court 1", 19, 21, team_players[3]), ("Badminton Court 2", 16, 18, players[7]),
            ("Badminton Court 3", 20, 22, team_players[2]), ("Volleyball Court", 17, 19, team_players[7]),
            ("Tennis Court", 7, 9, players[1]), ("Pickleball Court", 18, 20, team_players[6]),
            ("Table Tennis 1", 10, 13, team_players[5]),
        ]
        for name, start, end, player in plan_today:
            court = by_name[name]
            if end <= local_hour:
                status = S.COMPLETED
            elif start <= local_hour:
                status = S.CHECKED_IN
            else:
                status = random.choice([S.CONFIRMED, S.CONFIRMED, S.PENDING])
            r = make(court, today, start, end, player, status)
            if status == S.PENDING:
                r.payment_deadline = services.payment_deadline_for(r.start_dt)
                r.save(update_fields=["payment_deadline"])

        # A likely no-show: started 20 minutes ago, not checked in.
        if cfg.open_hour + 1 <= local_hour < cfg.close_hour - 1:
            court = by_name["Badminton Court 3"]
            if services.check_range(court, today, local_hour, local_hour + 1, allow_past=True)[0]:
                make(court, today, local_hour, local_hour + 1, players[3], S.CONFIRMED,
                     method=PaymentMethod.GCASH)

        # Demo player's bookings
        future = today + timedelta(days=7)
        r = make(by_name["Badminton Court 2"], future, 19, 21, demo_player, S.CONFIRMED, method=PaymentMethod.GCASH)
        r2 = make(by_name["Basketball A"], today + timedelta(days=12), 17, 19, demo_player, S.PENDING)
        r2.payment_deadline = now + timedelta(hours=20)
        r2.save(update_fields=["payment_deadline"])

        # Two cancellation requests waiting for approval
        for player, court_name, days_ahead, start in [(players[10], "Tennis Court", 1, 9), (team_players[6], "Pickleball Court", 4, 16)]:
            r = make(by_name[court_name], today + timedelta(days=days_ahead), start, start + 2, player,
                     S.CONFIRMED, method=PaymentMethod.MAYA)
            services.request_cancel(r)

        ActivityLog.objects.create(actor=staff["manager"], action="Loaded demo data", reference="Setup")
        self.stdout.write(f"Created {Reservation.objects.count()} sample bookings.")
