import re
import base64

ETH_PHONE_RE = re.compile(r'^\+?251(9\d{8}|[1-8]\d{7,8})$')  # permissive for Ethiopia; adjust if needed

def looks_like_base64(s: str) -> bool:
    if not s or not isinstance(s, str):
        return False
    try:
        # quick check: length multiple of 4 and only base64 chars + padding
        if len(s) % 4 != 0:
            return False
        base64.b64decode(s, validate=True)
        return True
    except Exception:
        return False

def normalize_phone(phone: str) -> str | None:
    """Return E.164-style phone (e.g. +2519...) if valid, else None"""
    if not phone:
        return None
    phone = phone.strip()
    # remove spaces, dashes, parentheses
    phone = re.sub(r'[^\d+]', '', phone)

    # If it starts with 0 (local style), convert to +251
    if phone.startswith("0"):
        phone = "+251" + phone.lstrip("0")

    # If it lacks + and starts with 251, add +
    if phone.startswith("251") and not phone.startswith("+"):
        phone = "+" + phone

    # Final validation
    if ETH_PHONE_RE.match(phone):
        return phone
    return None
