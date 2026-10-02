from django.conf import settings

from . import services
from .models import Refund


NAV_BY_URL = {
    "my_reservations": "my",
    "staff_schedule": "schedule",
    "staff_reservations": "reservations",
    "staff_walkin": "walkin",
    "staff_receipt": "walkin",
    "staff_league": "league",
    "staff_reports": "reports",
    "staff_courts": "courts",
    "staff_team": "team",
}


def rms(request):
    ctx = {
        "COMPLEX_NAME": settings.RMS_COMPLEX_NAME,
        "COMPLEX_LOCATION": settings.RMS_COMPLEX_LOCATION,
        "CONTACT_NUMBER": settings.RMS_CONTACT_NUMBER,
        "DEMO_MODE": settings.RMS_DEMO_MODE,
    }
    match = getattr(request, "resolver_match", None)
    url_name = (match.url_name if match else "") or ""
    ctx["nav"] = NAV_BY_URL.get(url_name, "book" if not url_name.startswith("staff") else "")
    user = getattr(request, "user", None)
    if user is not None and services.is_staff_member(user) and request.path.startswith("/staff"):
        ctx.update(
            {
                "nav_cancel_requests": Refund.objects.filter(status=Refund.Status.REQUESTED).count(),
                "nav_to_collect": services.to_collect_queryset().count(),
                "is_manager": services.is_manager(user),
            }
        )
    return ctx
