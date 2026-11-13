from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from mcpapi.schema_doc import render

DOC = Path(settings.BASE_DIR).parent / "fakturační aplikace" / "mcp-schema.md"


class Command(BaseCommand):
    help = "Zapíše verzovaný popis MCP nástrojů do fakturační aplikace/mcp-schema.md."

    def handle(self, *args, **options):
        DOC.write_text(render(), encoding="utf-8")
        self.stdout.write(f"Zapsáno: {DOC}")
