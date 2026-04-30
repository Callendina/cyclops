"""Exception types raised by the cyclops library."""


class CyclopsError(Exception):
    """Base class for all cyclops exceptions."""


class CyclopsConfigError(CyclopsError):
    """Required configuration is missing or invalid (e.g. unset env var)."""


class CyclopsValidationError(CyclopsError):
    """An event failed validation (event_type format, level enum, field collision)."""


class CyclopsForbiddenFieldError(CyclopsValidationError):
    """A caller-supplied field used a name that's on the forbidden list.

    The forbidden list catches accidental leakage of secrets, PANs, and similar
    sensitive values. The check is on the *field name* — if your code ever
    needs to log a value of one of these shapes, use a redaction helper
    (:mod:`cyclops.redact`) and pass the result under a non-forbidden name.
    """

    def __init__(self, field_name: str, *, path: str = "") -> None:
        location = f" at {path!r}" if path else ""
        super().__init__(
            f"Field name {field_name!r}{location} is on the forbidden list. "
            "If you need to log a value of this kind, redact it and use a "
            "different field name (see cyclops.redact_*)."
        )
        self.field_name = field_name
        self.path = path
