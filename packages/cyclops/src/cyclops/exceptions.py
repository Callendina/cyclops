"""Exception types raised by the cyclops library."""


class CyclopsError(Exception):
    """Base class for all cyclops exceptions."""


class CyclopsConfigError(CyclopsError):
    """Required configuration is missing or invalid (e.g. unset env var)."""


class CyclopsValidationError(CyclopsError):
    """An event failed validation (event_type format, level enum, field collision)."""
