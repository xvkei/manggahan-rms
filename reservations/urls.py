from django.urls import path

from . import views_player as player
from . import views_staff as staff

urlpatterns = [
    # Player side
    path("", player.home, name="home"),
    path("book/hold/", player.hold_slots, name="hold_slots"),
    path("book/verify/", player.verify_mobile, name="verify_mobile"),
    path("book/details/", player.details, name="booking_details"),
    path("pay/<uuid:token>/", player.pay, name="pay"),
    path("manage/<uuid:token>/", player.manage_booking, name="manage_booking"),
    path("manage/<uuid:token>/cancel/", player.cancel_booking, name="cancel_booking"),
    path("manage/<uuid:token>/reschedule/", player.reschedule_booking, name="reschedule_booking"),
    path("my/", player.my_reservations, name="my_reservations"),
    path("accounts/login/", player.player_login, name="player_login"),
    path("accounts/signup/", player.player_signup, name="player_signup"),
    path("accounts/logout/", player.logout_view, name="logout"),

    # Admin side (front desk and manager)
    path("staff/login/", staff.staff_login, name="staff_login"),
    path("staff/", staff.schedule, name="staff_schedule"),
    path("staff/r/<str:code>/action/", staff.reservation_action, name="staff_reservation_action"),
    path("staff/reservations/", staff.reservations, name="staff_reservations"),
    path("staff/refunds/<int:pk>/decide/", staff.refund_decide, name="staff_refund_decide"),
    path("staff/walk-in/", staff.walkin, name="staff_walkin"),
    path("staff/league/", staff.league, name="staff_league"),
    path("staff/sales/<str:code>/", staff.receipt, name="staff_receipt"),
    path("staff/reports/", staff.reports, name="staff_reports"),
    path("staff/reports/export.csv", staff.reports_export, name="staff_reports_export"),
    path("staff/courts/", staff.courts_rates, name="staff_courts"),
    path("staff/team/", staff.team, name="staff_team"),
    path("staff/team/activity.csv", staff.activity_export, name="staff_activity_export"),
]
