"""Single-use, time-limited password reset links for tenant accounts."""
import hashlib
import hmac
import json
import os
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

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
                 "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError("El proveedor rechazó el correo.")
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError("No fue posible enviar el correo de recuperación.") from exc
