"""Tenant lifecycle, subscriptions, and quotas."""
import re
from datetime import timedelta
from sqlalchemy import func, select
from .models import SaasPlan, Tenant, TenantSubscription, User, Product, Order, Audit, now

PLAN_SEEDS = (
    ('emprendedor', 'Emprendedor', 49900, 3, 20, 100),
    ('empresa', 'Empresa', 149900, 25, 200, 1000),
    ('marca-blanca', 'Marca blanca', 399900, 250, 1000, 10000),
)

def valid_slug(slug):
    return bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?', slug)) and slug != 'plataforma'

def seed_plans(db):
    for code, name, price, d, p, o in PLAN_SEEDS:
        if not db.scalar(select(SaasPlan).where(SaasPlan.code == code)):
            db.add(SaasPlan(code=code, name=name, price_cents=price, max_distributors=d, max_products=p, max_orders_month=o))
    db.flush()

def create_tenant(db, *, slug, name, email, plan_id, admin_name, password, trial_days=14):
    from .security import hash_password
    from .services import normalize_email
    slug = slug.strip().lower()
    name = name.strip()
    admin_name = admin_name.strip()
    email = normalize_email(email)
    if not valid_slug(slug) or not 3 <= len(name) <= 140 or not 2 <= len(admin_name) <= 120:
        raise ValueError('Nombre, identificador o responsable del espacio inválido.')
    if db.scalar(select(Tenant).where(Tenant.slug == slug)):
        raise ValueError('Ese identificador ya está registrado.')
    plan = db.get(SaasPlan, plan_id)
    if not plan or not plan.active:
        raise ValueError('Plan no disponible.')
    tenant = Tenant(name=name, brand_name=name, slug=slug, contact_email=email)
    db.add(tenant)
    db.flush()
    db.add(TenantSubscription(tenant_id=tenant.id, plan_id=plan.id, status='TRIAL', ends_at=now() + timedelta(days=trial_days)))
    admin = User(tenant_id=tenant.id, name=admin_name, email=email, password_hash=hash_password(password), role='admin', must_change_password=True)
    db.add(admin)
    db.flush()
    db.add(Audit(tenant_id=tenant.id, actor_id='system', action='tenant.created', entity_type='tenant', entity_id=tenant.id, detail=plan.code))
    db.commit()
    return tenant, admin

def get_subscription(db, tenant_id):
    return db.get(TenantSubscription, tenant_id)

def access_enabled(db, tenant_id):
    tenant = db.get(Tenant, tenant_id) if tenant_id else None
    sub = get_subscription(db, tenant_id) if tenant_id else None
    return bool(tenant and tenant.active and sub and sub.status in ('TRIAL', 'ACTIVA') and sub.ends_at >= now())

def ensure_active(db, tenant_id):
    if not access_enabled(db, tenant_id):
        raise ValueError('El espacio está suspendido o su suscripción venció.')

def get_plan(db, tenant_id):
    sub = get_subscription(db, tenant_id)
    if not sub:
        raise ValueError('No existe una suscripción para este espacio.')
    return db.get(SaasPlan, sub.plan_id)

def ensure_quota(db, tenant_id, resource):
    ensure_active(db, tenant_id)
    plan = get_plan(db, tenant_id)
    if resource == 'distributors':
        count = db.scalar(select(func.count()).select_from(User).where(User.tenant_id == tenant_id, User.role == 'distribuidor', User.active.is_(True))) or 0
        limit = plan.max_distributors
    elif resource == 'products':
        count = db.scalar(select(func.count()).select_from(Product).where(Product.tenant_id == tenant_id)) or 0
        limit = plan.max_products
    elif resource == 'orders':
        start = now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        count = db.scalar(select(func.count()).select_from(Order).where(Order.tenant_id == tenant_id, Order.created_at >= start)) or 0
        limit = plan.max_orders_month
    else:
        raise ValueError('Recurso no reconocido.')
    if count >= limit:
        raise ValueError(f'Se alcanzó el límite del plan para {resource}. Solicita una ampliación.')
    return limit - count
