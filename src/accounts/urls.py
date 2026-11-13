from django.urls import path

from reports.views import global_overview

from . import views

app_name = "accounts"
urlpatterns = [
    path("uzivatele/", views.users, name="users"),
    path("uzivatele/novy/", views.user_edit, name="user_create"),
    path("uzivatele/<int:pk>/", views.user_edit, name="user_edit"),
    path("profily/", views.profiles, name="profiles"),
    path("profily/novy/", views.profile_create, name="profile_create"),
    path("audit/", views.audit_log, name="audit"),
    path("souhrn/", global_overview, name="global"),
]
