from django.urls import path

from . import views

app_name = "invoicing"
urlpatterns = [
    path("", views.invoice_list, name="list"),
    path("nova/", views.invoice_create, name="create"),
    path("nahled/", views.preview_unsaved, name="preview"),
    path("soucty/", views.totals, name="totals"),
    path("<int:pk>/", views.invoice_detail, name="detail"),
    path("<int:pk>/upravit/", views.invoice_edit, name="edit"),
    path("<int:pk>/smazat/", views.invoice_delete, name="delete"),
    path("<int:pk>/nahled.pdf", views.preview_draft, name="preview_draft"),
    path("<int:pk>/vystavit/", views.issue_confirm, name="issue"),
    path("<int:pk>/faktura.pdf", views.invoice_pdf, name="pdf"),
    path("<int:pk>/storno/", views.cancel, name="cancel"),
    path("<int:pk>/platby/", views.payment_add, name="payment_add"),
    path("<int:pk>/platby/<int:payment_pk>/zrusit/", views.payment_reverse, name="payment_reverse"),
    path("<int:pk>/predano/", views.delivered, name="delivered"),
    path("<int:pk>/upominka/", views.reminder, name="reminder"),
]
