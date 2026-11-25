"""Single-use, time-limited password reset links for tenant accounts."""
import hashlib
import logging
import re
import hmac
import json
import os
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger('ezteams.password_reset')
RESET_TTL_SECONDS = 1800


def make_reset_token(user, secret: str):
    expires = int(time.time()) + RESET_TTL_SECONDS
    payload = f"{user.id}.{expires}"
    key = hmac.new(secret.encode(), user.password_hash.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_reset_token(token: str, user, secret: str):
    if not token or len(token) > 200 or user is None:
        return False
    try:
        user_id, expires_text, signature = token.split(".", 2)
        expires = int(expires_text)
        if not (int(time.time()) <= expires <= int(time.time()) + RESET_TTL_SECONDS):
            return False
        if not hmac.compare_digest(user.id, user_id):
            return False
        payload = f"{user_id}.{expires_text}"
        key = hmac.new(secret.encode(), user.password_hash.encode(), hashlib.sha256).digest()
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature, expected)
    except (TypeError, ValueError):
        return False


def mail_configured():
    return bool(os.getenv("RESEND_API_KEY") and os.getenv("RESEND_FROM_EMAIL"))


def send_reset_email(recipient: str, url: str):
    """Use server-side Resend API; never expose the API key or reset link in logs."""
    if not mail_configured():
        raise RuntimeError("Falta configurar el servicio de correo de recuperación.")
    body = {
        "from": os.environ["RESEND_FROM_EMAIL"],
        "to": [recipient],
        "subject": "EZTeams — Restablecer contraseña",
        "text": ("Se solicitó restablecer la contraseña de tu cuenta EZTeams. "
                 "Abre el siguiente enlace para establecer una nueva contraseña:\n\n"
                 f"{url}\n\n"
                 "El enlace vence en 30 minutos y solo puede usarse una vez. "
                 "Si no lo solicitaste, ignora este correo."),
    }
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": "Bearer " + os.environ["RESEND_API_KEY"],
                 "Content-Type": "application/json",
                 "User-Agent": "EZTeams/1.0 (transactional-email; https://ezteams.xyz)",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=8) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError("El proveedor rechazó el correo.")
            logger.info("Resend accepted the recovery message (HTTP %s).", response.status)
    except HTTPError as exc:
        # Status/error type only. Never log address, reset URL, or API key.
        error_type = "unknown"
        error_reason = "unspecified"
        raw = exc.read(2048).decode('utf-8', errors='replace')
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                error_type = str(data.get("name", data.get("error", "unknown")))[:64]
                error_reason = str(data.get("message", "unspecified"))
        except ValueError:
            # Provider-side HTML/WAF errors are not JSON. Extract a short
            # diagnostic snippet without persisting secrets or email addresses.
            error_type = 'non_json_response'
            error_reason = re.sub(r'<[^>]*>', ' ', raw)[:300]
        error_reason = re.sub(r"[\\w.+-]+@[\\w.-]+", "[redacted-email]", error_reason)
        error_reason = error_reason.replace(os.environ["RESEND_API_KEY"], "[redacted-secret]")[:250]
        logger.error("Resend rejected password recovery: HTTP %s, type=%s, message=%s", exc.code, error_type, error_reason)
        raise RuntimeError(f"Resend HTTP {exc.code}; {error_type}; {error_reason}") from exc
    except (URLError, TimeoutError) as exc:
        logger.error("Resend could not be reached: %s", type(exc).__name__)
        raise RuntimeError("No fue posible contactar al proveedor de correo.") from exc
