"""
Run with:  python manage.py test
"""
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import pricing, services
from .models import Court, Player, PlayerType, Promo, Refund, Reservation, SiteSettings, Sport


class PricingRulesTests(TestCase):
    """Pure business rules (no database needed, but run inside the normal test suite)."""

    def test_student_evening_badminton(self):
        q = pricing.build_quote([19, 20], 250, 300, 18, 10, "Student")
        self.assertEqual(q["court_fee"], Decimal("600.00"))
        self.assertEqual(q["discount"], Decimal("60.00"))
        self.assertEqual(q["total"], Decimal("540.00"))

    def test_discounts_do_not_stack(self):
        promo = {9: (20, "Weekday mornings"), 10: (20, "Weekday mornings")}
        q = pricing.build_quote([9, 10], 250, 300, 18, 10, "Student", promo_by_hour=promo)
        self.assertEqual(q["total"], Decimal("400.00"))  # 20% promo beats 10% student

    def test_day_and_evening_mix(self):
        q = pricing.build_quote([17, 18], 500, 600, 18, 10, "Student")
        self.assertEqual(q["total"], Decimal("990.00"))

    def test_free_hour_and_equipment(self):
        q = pricing.build_quote([19, 20], 250, 300, 18, equipment_lines=[("Racket", 2, 50)], redeem_free_hour=True)
        self.assertEqual(q["total"], Decimal("400.00"))

    def test_refund_tiers(self):
        self.assertEqual(pricing.refund_tier(30), ("full", 100))
        self.assertEqual(pricing.refund_tier(5), ("half", 50))
        self.assertEqual(pricing.refund_tier(-1), ("none", 0))
        self.assertEqual(pricing.refund_tier(1, cancelled_by_complex=True), ("complex", 100))
        self.assertEqual(pricing.refund_amount(270, 50), Decimal("135.00"))

    def test_points(self):
        self.assertEqual(pricing.points_for(540), 5)
        self.assertEqual(pricing.points_for(99), 0)

    def test_mobile_normalizing(self):
        self.assertEqual(services.normalize_mobile("0917 123 4567"), "09171234567")
        self.assertEqual(services.normalize_mobile("+63 917 123 4567"), "09171234567")
        self.assertIsNone(services.normalize_mobile("12345"))


class BookingFlowTests(TestCase):
    def setUp(self):
        SiteSettings.get()
        self.court = Court.objects.create(name="Badminton Court 1", sport=Sport.BADMINTON, day_rate=250, evening_rate=300)
        self.player = Player.objects.create(name="Test Player", mobile="09170000000", player_type=PlayerType.WALKIN)
        self.tomorrow = timezone.localdate() + timedelta(days=1)

    def _book(self, start, end, status=Reservation.Status.CONFIRMED, paid=True):
        q = services.quote(self.court, self.tomorrow, start, end, PlayerType.WALKIN)
        return Reservation.objects.create(
            player=self.player, court=self.court, date=self.tomorrow, start_hour=start, end_hour=end,
            player_type=PlayerType.WALKIN, court_fee=q["court_fee"], total=q["total"],
            amount_paid=q["total"] if paid else 0, status=status, payment_method="gcash",
        )

    def test_double_booking_is_blocked(self):
        self._book(19, 21)
        ok, _ = services.check_range(self.court, self.tomorrow, 20, 22)
        self.assertFalse(ok)
        ok, _ = services.check_range(self.court, self.tomorrow, 17, 19)
        self.assertTrue(ok)

    def test_promo_applies(self):
        Promo.objects.create(name="All day", percent=50, weekdays="0,1,2,3,4,5,6", start_hour=6, end_hour=22)
        q = services.quote(self.court, self.tomorrow, 8, 9, PlayerType.WALKIN)
        self.assertEqual(q["total"], Decimal("125.00"))

    def test_cancel_creates_refund_request(self):
        r = self._book(19, 21)
        refund = services.request_cancel(r)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.Status.CANCEL_REQUESTED)
        self.assertEqual(refund.status, Refund.Status.REQUESTED)
        self.assertIn(refund.percent, (50, 100))
        ok, _ = services.check_range(self.court, self.tomorrow, 19, 21)
        self.assertTrue(ok)  # slot is released while the refund waits

    def test_unpaid_booking_is_auto_released(self):
        r = self._book(19, 21, status=Reservation.Status.PENDING, paid=False)
        r.payment_deadline = timezone.now() - timedelta(minutes=1)
        r.save()
        services.run_housekeeping(force=True)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.Status.RELEASED)

    def test_block_court_refunds_in_full(self):
        r = self._book(19, 21)
        affected, total = services.block_court(self.court, self.tomorrow, 18, 22, "Net repair", None)
        r.refresh_from_db()
        self.assertEqual(r.status, Reservation.Status.CANCELLED)
        self.assertEqual(total, Decimal("600.00"))

    def test_home_page_loads(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Book a court")

    def test_staff_pages_need_login(self):
        response = self.client.get(reverse("staff_schedule"))
        self.assertEqual(response.status_code, 302)
