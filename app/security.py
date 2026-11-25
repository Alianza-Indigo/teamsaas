import hashlib
import hmac
import os
import secrets
from datetime import timedelta
from .models import Session, User, now

SESSION_COOKIE = 'indigo_session'
SESSION_DAYS = 7


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError('La contraseña debe tener al menos 12 caracteres.')
    salt = os.urandom(16)
    iterations = 330_000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, iterations)
    return f'pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, rounds, salt, expected = stored.split('$')
        if kind != 'pbkdf2_sha256':
            return False
        got = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(got, bytes.fromhex(expected))
    except (ValueError, TypeError):
        return False


def token_digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def create_session(db, user: User):
    raw = secrets.token_urlsafe(42)
    csrf = secrets.token_urlsafe(32)
    # Bind platform sessions to the current environment password, so changing
    # SUPERADMIN_PASSWORD also invalidates old sessions without storing it.
    digest = token_digest(raw)
    if user.role == 'superadmin' and user.tenant_id is None and os.getenv('SUPERADMIN_PASSWORD'):
        digest = hmac.new(os.environ['SUPERADMIN_PASSWORD'].encode(), raw.encode(), hashlib.sha256).hexdigest()
    session = Session(token_hash=digest, user_id=user.id, csrf_token=csrf, expires_at=now() + timedelta(days=SESSION_DAYS))
    db.add(session)
    db.commit()
    return raw


def read_session(db, raw: str):
    if not raw:
        return None, None
    env_password = os.getenv('SUPERADMIN_PASSWORD')
    if env_password:
        digest = hmac.new(env_password.encode(), raw.encode(), hashlib.sha256).hexdigest()
        session = db.get(Session, digest)
        if session and session.expires_at >= now():
            user = db.get(User, session.user_id)
            if user and user.active and user.role == 'superadmin' and user.tenant_id is None:
                return user, session
    session = db.get(Session, token_digest(raw))
    if not session or session.expires_at < now():
        return None, None
    user = db.get(User, session.user_id)
    if not user or not user.active or (env_password and user.role == 'superadmin' and user.tenant_id is None):
        return None, None
    return user, session


def clear_session(db, raw: str):
    if raw:
        entry = db.get(Session, token_digest(raw))
        if not entry and os.getenv('SUPERADMIN_PASSWORD'):
            digest = hmac.new(os.environ['SUPERADMIN_PASSWORD'].encode(), raw.encode(), hashlib.sha256).hexdigest()
            entry = db.get(Session, digest)
        if entry:
            db.delete(entry)
            db.commit()
