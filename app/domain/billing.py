"""Cobro manual del SaaS, separado de la administración de empresas.

El registro representa una confirmación humana de pago externo; no procesa
cargos bancarios. Cada tenant y periodo tiene a lo sumo un cargo.
"""
import re
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ..models import User, SaasPlan, Tenant, TenantCharge, TenantSubscription, now
from .common import audit
from .renewals import next_date


def record_saas_charge(db, actor_id, tenant_id, period, reference):
    admin = db.get(User, actor_id)
    if not admin or not admin.active or admin.role != 'superadmin' or admin.tenant_id is not None:
        raise ValueError('Solo el superadministrador puede registrar mensualidades SaaS.')
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError('La empresa no existe.')
    if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
        raise ValueError('Periodo de cobro inválido.')
    reference = reference.strip()
    if not 2 <= len(reference) <= 120:
        raise ValueError('Referencia de cobro inválida.')
    # Serie de cobros serializada con bloqueo en Postgres.
    sub = db.scalar(select(TenantSubscription).where(
        TenantSubscription.tenant_id == tenant_id).with_for_update())
    if not sub:
        raise ValueError('La empresa no tiene suscripción.')
    existing = db.scalar(select(TenantCharge).where(
        TenantCharge.tenant_id == tenant_id, TenantCharge.period_key == period))
    if existing:
        if existing.payment_reference == reference:
            return existing  # idempotencia: mismo comprobante, mismo periodo
        raise ValueError('Ese periodo de la empresa ya fue cobrado.')
    plan = db.get(SaasPlan, sub.plan_id)
    if not plan or not plan.active:
        raise ValueError('No hay plan vigente para cobrar.')
    charge = TenantCharge(tenant_id=tenant_id, plan_id=plan.id, period_key=period,
                          amount_cents=plan.price_cents, currency=plan.currency,
                          payment_reference=reference, recorded_by=actor_id)
    db.add(charge)
    sub.status = 'ACTIVA'
    sub.ends_at = next_date(max(sub.ends_at, now()), 'MENSUAL')
    sub.updated_at = now()
    audit(db, None, actor_id, 'platform.charge_recorded', 'tenant', tenant_id,
          f'{period} {reference}')
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError('Pago de suscripción repetido; no se creó un segundo cargo.') from exc
    return charge


def monthly_recurring_revenue(db):
    """Proyección MRR contractual, no efectivo cobrado."""
    rows = db.execute(select(TenantSubscription, Tenant, SaasPlan)
        .join(Tenant, Tenant.id == TenantSubscription.tenant_id)
        .join(SaasPlan, SaasPlan.id == TenantSubscription.plan_id)).all()
    totals = {}
    for sub, tenant, plan in rows:
        if tenant.active and sub.status == 'ACTIVA' and sub.ends_at >= now():
            totals[plan.currency] = totals.get(plan.currency, 0) + plan.price_cents
    return totals
