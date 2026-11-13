from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Idempotentně založí výchozí data (správce, fakturační profily)."

    def handle(self, *args, **options):
        self.stdout.write("bootstrap: zatím nic k založení")
