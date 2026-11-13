from django.urls import path

from . import views

app_name = "partners"
urlpatterns = [
    path("", views.partner_list, name="list"),
    path("novy/", views.partner_edit, name="create"),
    path("<int:pk>/", views.partner_detail, name="detail"),
    path("<int:pk>/upravit/", views.partner_edit, name="edit"),
    path("<int:pk>/kopirovat/", views.partner_copy, name="copy"),
]
