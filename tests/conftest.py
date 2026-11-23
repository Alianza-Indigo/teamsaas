import os
import tempfile

ROOT = tempfile.mkdtemp(prefix='teamindigo-saas-test-')
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(ROOT, 'tests.db')
os.environ['AUTO_CREATE_TABLES'] = 'true'
os.environ['FORM_SECRET'] = 'a-test-form-secret-not-for-production'

import pytest
from datetime import timedelta
from sqlalchemy import select
from app.db import Base, SessionLocal, engine
from app.models import Tenant, TenantSubscription, User, Product, SaasPlan, now
from app.saas import seed_plans
from app.security import hash_password, token_digest
from app.models import Session as LoginSession


@pytest.fixture(autouse=True)
def fixture_data():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_plans(db)
        plan = db.scalar(select(SaasPlan).where(SaasPlan.code == 'empresa'))
        db.add_all([
            Tenant(id='tenant-a', slug='equipo-a', name='Equipo A', contact_email='admin@a.com'),
            Tenant(id='tenant-b', slug='equipo-b', name='Equipo B', contact_email='admin@b.com'),
        ])
        db.flush()
        for t in ('tenant-a', 'tenant-b'):
            db.add(TenantSubscription(tenant_id=t, plan_id=plan.id, status='ACTIVA', ends_at=now()+timedelta(days=90)))
        db.add_all([
            User(id='super-id', tenant_id=None, name='Super Admin', email='super@global.test', password_hash=hash_password('Strong-Super-Password-2026'), role='superadmin'),
            User(id='admin-a', tenant_id='tenant-a', name='Admin A', email='admin@example.com', password_hash=hash_password('Strong-Admin-Password-2026'), role='admin'),
            User(id='admin-b', tenant_id='tenant-b', name='Admin B', email='admin@example.com', password_hash=hash_password('Strong-Admin-Password-2026'), role='admin'),
            User(id='dist-a', tenant_id='tenant-a', name='Distribuidor A', email='seller@example.com', password_hash=hash_password('Strong-Seller-Password-2026'), role='distribuidor', referral_code='REF-A'),
            User(id='dist-b', tenant_id='tenant-b', name='Distribuidor B', email='seller@example.com', password_hash=hash_password('Strong-Seller-Password-2026'), role='distribuidor', referral_code='REF-B'),
        ])
        db.add_all([
            Product(id='prod-a', tenant_id='tenant-a', name='Servicio exclusivo A', price_cents=7500, currency='USD', billing_interval='MENSUAL', commission_bps=2000, active=True),
            Product(id='prod-b', tenant_id='tenant-b', name='Servicio exclusivo B', price_cents=8500, currency='MXN', billing_interval='MENSUAL', commission_bps=3000, active=True),
        ])
        db.commit()
    yield


def login(client, workspace='equipo-a', role='admin'):
    details = {
        'admin': ('admin@example.com', 'Strong-Admin-Password-2026'),
        'distribuidor': ('seller@example.com', 'Strong-Seller-Password-2026'),
        'superadmin': ('super@global.test', 'Strong-Super-Password-2026'),
    }
    email, password = details[role]
    from app.main import app
    page = client.get('/ingresar?espacio='+workspace)
    assert page.status_code == 200
    r = client.post('/ingresar', data={'email': email, 'password': password, 'workspace': workspace, 'login_csrf': client.cookies['login_csrf']}, follow_redirects=False)
    assert r.status_code == 303, r.text
    assert 'indigo_session' in client.cookies
    return r


def csrf(client):
    with SessionLocal() as db:
        return db.get(LoginSession, token_digest(client.cookies['indigo_session'])).csrf_token
