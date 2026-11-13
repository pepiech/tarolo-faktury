from django.urls import include, path

from mcpapi.views import endpoint as mcp_endpoint

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("health/", views.health, name="health"),
    path("mcp", mcp_endpoint),
    path("mcp/", mcp_endpoint, name="mcp"),
    path("sprava/", include("accounts.urls")),
    path("p/<slug:slug>/", include("reports.urls")),
    path("p/<slug:slug>/faktury/", include("invoicing.urls")),
    path("p/<slug:slug>/partneri/", include("partners.urls")),
    path("p/<slug:slug>/nastaveni/", include("profiles.urls")),
]
