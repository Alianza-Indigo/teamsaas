"""One-time database initialization. Run outside the web server before deployment."""
import os
from datetime import timedelta
from sqlalchemy import select
from .db import Base, SessionLocal, engine
from .models import Product, User, Tenant, SaasPlan, TenantSubscription, now
from .security import hash_password
from .services import normalize_email
from .saas import seed_plans, create_tenant


def bootstrap():
    platform_email = normalize_email(os.environ['BOOTSTRAP_PLATFORM_EMAIL'])
    platform_password = os.environ['BOOTSTRAP_PLATFORM_PASSWORD']
    owner_email = normalize_email(os.environ['BOOTSTRAP_ADMIN_EMAIL'])
    owner_password = os.environ['BOOTSTRAP_ADMIN_PASSWORD']
    if platform_email == owner_email:
        raise ValueError('Utiliza correos distintos para el superadministrador y la cuenta de Team Índigo.')
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_plans(db)
        db.commit()
        root = db.scalar(select(User).where(User.email == platform_email, User.tenant_id.is_(None)))
        if root is None:
            db.add(User(name='Superadministrador', email=platform_email, password_hash=hash_password(platform_password), role='superadmin'))
        elif root.role != 'superadmin':
            raise RuntimeError('Ese correo no es superadministrador.')
        db.commit()
        tenant = db.scalar(select(Tenant).where(Tenant.slug == 'team-indigo'))
        if tenant is None:
            plan = db.scalar(select(SaasPlan).where(SaasPlan.code == 'empresa'))
            tenant, _ = create_tenant(db, slug='team-indigo', name='Team Índigo', email=owner_email,
                                      admin_name='Administrador', plan_id=plan.id, password=owner_password)
            sub = db.get(TenantSubscription, tenant.id)
            sub.status = 'ACTIVA'
            sub.ends_at = now() + timedelta(days=3650)
        else:
            owner = db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == owner_email))
            if owner is None:
                db.add(User(tenant_id=tenant.id, name='Administrador', email=owner_email, password_hash=hash_password(owner_password), role='admin', must_change_password=True))
            elif owner.role != 'admin':
                raise RuntimeError('Ese correo no tiene rol administrador en Team Índigo.')
        for name, description, cents in [
            ('Claude 6.25x', 'Precio de referencia. Verificar condiciones y autorización comercial.', 7500),
            ('ChatGPT', 'Definir plan, precio y condiciones aplicables.', None),
            ('Google Workspace', 'Definir edición, precio, usuarios y condiciones.', None),
        ]:
            if not db.scalar(select(Product).where(Product.tenant_id == tenant.id, Product.name == name)):
                db.add(Product(tenant_id=tenant.id, name=name, description=description, price_cents=cents, currency='USD', active=False))
        db.commit()
    print('Team Índigo SaaS: planes, espacio propio y superadministrador inicializados.')


if __name__ == '__main__':
    bootstrap()
