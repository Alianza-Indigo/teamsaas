"""Reglas y utilidades compartidas de los motores comerciales."""
import re
from decimal import Decimal, ROUND_HALF_UP
from ..models import Audit, User
from ..saas import ensure_active

INTERVALS = ('UNICO', 'MENSUAL', 'ANUAL')
STATUSES = ('PENDIENTE', 'CONFIRMADO', 'PAGADO', 'EN_PROCESO', 'ACTIVO', 'CANCELADO')
TRANSITIONS = {
    'PENDIENTE': {'CONFIRMADO', 'CANCELADO'},
    'CONFIRMADO': {'CANCELADO'},
    'PAGADO': {'EN_PROCESO', 'ACTIVO', 'CANCELADO'},
    'EN_PROCESO': {'ACTIVO', 'CANCELADO'},
    'ACTIVO': {'CANCELADO'},
    'CANCELADO': set(),
}


def money(cents, currency='USD'):
    return f'{(cents or 0)/100:,.2f} {currency}'


def commission_cents(total, bps):
    return int((Decimal(total) * Decimal(bps) / Decimal(10000)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def normalize_email(email):
    value = email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value) or len(value) > 254:
        raise ValueError('Correo electrónico inválido.')
    return value


def valid_product(product):
    return bool(product and product.active and product.price_cents is not None and product.price_cents > 0
                and product.billing_interval in INTERVALS and 0 <= product.commission_bps <= 10000)


def actor_for(db, tenant_id, actor_id, roles=('admin', 'distribuidor')):
    ensure_active(db, tenant_id)
    actor = db.get(User, actor_id)
    if not actor or not actor.active or actor.tenant_id != tenant_id or actor.role not in roles:
        raise ValueError('Actor sin autorización para esta operación.')
    return actor


def audit(db, tenant_id, actor_id, action, entity_type, entity_id, detail=''):
    db.add(Audit(tenant_id=tenant_id, actor_id=actor_id, action=action,
                 entity_type=entity_type, entity_id=entity_id, detail=detail))
