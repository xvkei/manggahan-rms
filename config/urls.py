from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Manggahan RMS database admin"
admin.site.site_title = "Manggahan RMS"

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("", include("reservations.urls")),
]
