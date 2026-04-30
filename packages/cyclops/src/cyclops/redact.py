"""Pure-function redaction helpers.

Designed to be called *by the app* before passing sensitive values into an
event. They take the raw value, return a masked string, and have no side
effects.

The library does not auto-redact — it can't know which fields are sensitive
without explicit guidance, and silently scrubbing creates the illusion of
safety. Use these helpers at the call site:

    cyclops.event(
        "vispay.payment.processed",
        outcome="success",
        masked_pan=cyclops.redact_pan(card.number),
        user=cyclops.redact_email(user.email),
    )

The output forms aim to be:
- *Identifiable* — a human triaging logs can match a redacted PAN to a
  customer record by checking the last 4 against the database, without ever
  seeing full card data in logs.
- *Stable* — the same input redacts to the same output, so dashboards can
  group on the masked value when needed.
- *Safe* — no helper ever returns the unmasked value, even for short inputs.
"""

from __future__ import annotations

_MASK = "***"


def redact_pan(value: object) -> str:
    """Mask a PAN: keep first 6 + last 4 digits, mask the middle.

    Industry convention for payment-card logging. For values too short to
    leave any masked middle, falls back to ``"***<last4>"``.
    """
    s = str(value)
    if len(s) <= 10:
        return _MASK + s[-4:] if len(s) >= 4 else _MASK
    middle = "*" * (len(s) - 10)
    return s[:6] + middle + s[-4:]


def redact_email(value: object) -> str:
    """Mask an email: keep the first character of the local-part and the domain.

    ``"alice@example.com"`` → ``"a***@example.com"``. Inputs without an ``@``
    are returned as ``"***"`` — no partial reveal.
    """
    s = str(value)
    if "@" not in s:
        return _MASK
    local, _, domain = s.partition("@")
    if not local:
        return f"{_MASK}@{domain}"
    return f"{local[0]}{_MASK}@{domain}"


def redact_token(value: object) -> str:
    """Mask a token / API key: keep the last 4 characters, mask the rest.

    ``"sk_live_abcdef1234"`` → ``"***1234"``. Useful for capturing "is this the
    same token I had before" without storing the value itself. Tokens shorter
    than 4 characters get fully masked.
    """
    s = str(value)
    if len(s) < 4:
        return _MASK
    return _MASK + s[-4:]


__all__ = ["redact_email", "redact_pan", "redact_token"]
