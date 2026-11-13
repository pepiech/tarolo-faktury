class ToolError(Exception):
    """Strukturovaná chyba nástroje: stabilní kód, zpráva pro uživatele a podrobnosti."""

    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def as_dict(self):
        return {"error": {"code": self.code, "message": self.message, **self.details}}


# Stabilní kódy chyb (dokumentované v mcp-schema.md)
INVALID_ARGUMENTS = "invalid_arguments"
PROFILE_REQUIRED = "profile_required"
NOT_FOUND = "not_found"
FORBIDDEN = "forbidden"
AMBIGUOUS = "ambiguous"
VALIDATION_ERROR = "validation_error"
VERSION_CONFLICT = "version_conflict"
CONFIRMATION_REQUIRED = "confirmation_required"
IDEMPOTENCY_CONFLICT = "idempotency_conflict"
EXTERNAL_SERVICE = "external_service_error"
