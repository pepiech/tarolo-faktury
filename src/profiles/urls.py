from django.urls import path

from . import views

app_name = "profiles"
urlpatterns = [
    path("", views.settings_page, name="settings"),
    path("upravit/", views.profile_edit, name="edit"),
    path("logo/", views.logo_upload, name="logo"),
    path("logo/soubor/", views.logo_file, name="logo_file"),
    path("rady/<int:pk>/posunout/", views.series_shift, name="series_shift"),
    path("<str:kind>/novy/", views.item_edit, name="item_create"),
    path("<str:kind>/<int:pk>/", views.item_edit, name="item_edit"),
    path("<str:kind>/<int:pk>/smazat/", views.item_delete, name="item_delete"),
]
