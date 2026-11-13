from django.db import models


class PaymentMethod(models.TextChoices):
    BANK_TRANSFER = "bank_transfer", "Převodním příkazem"
    CASH = "cash", "Hotově"
    CARD = "card", "Platební kartou"
    OTHER = "other", "Jinak"
