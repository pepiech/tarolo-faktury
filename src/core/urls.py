from django.urls import include, path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("sprava/", include("accounts.urls")),
    path("p/<slug:slug>/", include("reports.urls")),
    path("p/<slug:slug>/faktury/", include("invoicing.urls")),
    path("p/<slug:slug>/partneri/", include("partners.urls")),
    path("p/<slug:slug>/nastaveni/", include("profiles.urls")),
]
