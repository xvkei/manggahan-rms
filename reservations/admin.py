from django.contrib import admin

from . import models


@admin.register(models.Court)
class CourtAdmin(admin.ModelAdmin):
    list_display = ("name", "sport", "day_rate", "evening_rate", "is_active", "sort_order")
    list_editable = ("day_rate", "evening_rate", "is_active", "sort_order")


@admin.register(models.Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ("code", "player", "court", "date", "start_hour", "end_hour", "status", "total", "amount_paid")
    list_filter = ("status", "source", "court__sport", "date")
    search_fields = ("code", "player__name", "player__mobile")


@admin.register(models.Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("name", "mobile", "player_type", "loyalty_points", "no_show_count", "member_until")
    search_fields = ("name", "mobile")


for model in (
    models.SiteSettings, models.Item, models.Promo, models.MembershipPlan, models.StaffProfile,
    models.PlayerPackage, models.LeagueBooking, models.ReservationItem, models.Refund, models.Payment,
    models.MaintenanceBlock, models.Sale, models.SaleLine, models.ActivityLog, models.SmsMessage,
    models.PhoneVerification,
):
    admin.site.register(model)
