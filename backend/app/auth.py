import base64
import binascii
import hmac

from .config import settings


def is_authorized(header: str | None) -> bool:
    if not settings.app_username or not settings.app_password:
        return settings.app_environment.casefold() != "production"
    if not header:
        return False
    scheme, separator, token = header.partition(" ")
    if not separator or scheme.casefold() != "basic":
        return False
    try:
        decoded = base64.b64decode(token, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    username, separator, password = decoded.partition(":")
    return bool(separator and hmac.compare_digest(username, settings.app_username) and hmac.compare_digest(password, settings.app_password))
