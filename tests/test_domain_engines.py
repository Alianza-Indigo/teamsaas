"""Pruebas de los motores comerciales separados del CRUD."""
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.db import SessionLocal
from app.main import app
from app.models import Audit, Commission, Order, Payment, SaasPlan, Tenant, TenantCharge
from app.domain.billing import monthly_recurring_revenue, record_saas_charge
from app.domain.orders import make_order, change_order_status
from app.domain.payments import record_payment
from app.domain.renewals import next_date
from app.domain.commissions import change_commission_status
from .conftest import login, csrf


def order_confirmed(db):
    o = make_order(db, 'tenant-a', 'dist-a', 'dist-a', 'prod-a',
                   'Cliente válido', 'c@example.com', '', 1)
    change_order_status(db, 'tenant-a', 'admin-a', o, 'CONFIRMADO')
    return o


def test_seller_cannot_impersonate_another_seller_or_confirm_orders():
    with SessionLocal() as db:
        with pytest.raises(ValueError, match='distribuidor no puede'):
            make_order(db, 'tenant-a', 'dist-a', 'dist-b', 'prod-a',
                       'Cliente válido', 'c@example.com', '', 1)
        o = make_order(db, 'tenant-a', 'admin-a', 'dist-a', 'prod-a',
                       'Cliente válido', 'c@example.com', '', 1)
        with pytest.raises(ValueError, match='Actor sin autorización'):
            change_order_status(db, 'tenant-a', 'dist-a', o, 'CONFIRMADO')
        assert o.status == 'PENDIENTE'


def test_payment_is_atomic_and_retries_are_idempotent():
    with SessionLocal() as db:
        order = order_confirmed(db)
        first, first_commission = record_payment(db, 'tenant-a', 'admin-a', order, 'BANK-001')
        retry, retry_commission = record_payment(db, 'tenant-a', 'admin-a', order, 'BANK-001')
        assert retry.id == first.id
        assert retry_commission.id == first_commission.id
        assert db.scalar(select(func.count()).select_from(Payment)) == 1
        assert db.scalar(select(func.count()).select_from(Commission)) == 1
        assert db.scalar(select(func.count()).select_from(Audit).where(Audit.action == 'commission.generated')) == 1
        with pytest.raises(ValueError):
            record_payment(db, 'tenant-a', 'admin-a', order, 'BANK-002')
        with pytest.raises(ValueError):
            change_commission_status(db, 'tenant-a', 'dist-a', first_commission, 'APROBADA')
        change_commission_status(db, 'tenant-a', 'admin-a', first_commission, 'APROBADA')
        with pytest.raises(ValueError):
            change_commission_status(db, 'tenant-a', 'admin-a', first_commission, 'PAGADA')
        change_commission_status(db, 'tenant-a', 'admin-a', first_commission, 'PAGADA', 'TRANSFER-001')
        assert first_commission.payout_reference == 'TRANSFER-001'
        assert db.scalar(select(func.count()).select_from(Commission)) == 1


def test_late_monthly_renewal_keeps_original_billing_anchor():
    with SessionLocal() as db:
        order = order_confirmed(db)
        record_payment(db, 'tenant-a', 'admin-a', order, 'ORIGINAL-001')
        # Simula una venta real de enero 31 atrasada varios meses.
        order.paid_at = datetime(2026, 1, 31, 9, 0)
        order.next_renewal_date = datetime(2026, 2, 28, 9, 0)
        db.commit()
        p, c = record_payment(db, 'tenant-a', 'admin-a', order, 'FEB-001',
                              kind='RENOVACION', period_key='2026-02')
        assert order.next_renewal_date == datetime(2026, 3, 31, 9, 0)
        assert p.period_key == '2026-02'
        assert c.amount_cents == 1500
        p2, _ = record_payment(db, 'tenant-a', 'admin-a', order, 'FEB-001',
                                kind='RENOVACION', period_key='2026-02')
        assert p2.id == p.id
        assert order.next_renewal_date.month == 3  # reintento no avanza el ciclo
        record_payment(db, 'tenant-a', 'admin-a', order, 'MAR-001',
                       kind='RENOVACION', period_key='2026-03')
        assert order.next_renewal_date.day == 30
        assert order.next_renewal_date.month == 4
        assert db.scalar(select(func.count()).select_from(Payment)) == 3
        assert db.scalar(select(func.count()).select_from(Commission)) == 3


def test_renewal_date_validates_interval_and_leap_year():
    assert next_date(datetime(2024, 2, 29), 'ANUAL').day == 28
    assert next_date(datetime(2025, 2, 28), 'MENSUAL', 31) == datetime(2025, 3, 31)
    with pytest.raises(ValueError):
        next_date(datetime(2026, 1, 1), 'UNICO')


def test_saas_charges_are_scoped_atomic_and_idempotent():
    with SessionLocal() as db:
        with pytest.raises(ValueError, match='superadministrador'):
            record_saas_charge(db, 'admin-a', 'tenant-b', '2026-11', 'REF-001')
        with pytest.raises(ValueError):
            record_saas_charge(db, 'super-id', 'tenant-a', '2030-13', 'REF-001')
        one = record_saas_charge(db, 'super-id', 'tenant-a', '2026-11', 'REF-001')
        two = record_saas_charge(db, 'super-id', 'tenant-a', '2026-11', 'REF-001')
        assert one.id == two.id
        with pytest.raises(ValueError, match='ya fue cobrado'):
            record_saas_charge(db, 'super-id', 'tenant-a', '2026-11', 'REF-002')
        other = record_saas_charge(db, 'super-id', 'tenant-b', '2026-11', 'REF-B')
        assert other.id != one.id
        assert db.scalar(select(func.count()).select_from(TenantCharge)) == 2
        assert 'MXN' in monthly_recurring_revenue(db)


def test_superadmin_login_page_redirects_to_platform():
    with TestClient(app) as root:
        login(root, 'plataforma', 'superadmin')
        response = root.get('/ingresar', follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'] == '/plataforma'


def test_seller_cannot_pay_an_order_by_calling_admin_endpoint():
    with TestClient(app) as seller:
        login(seller, role='distribuidor')
        assert seller.post('/admin/pedidos/nope/pago', data={
            'csrf': csrf(seller), 'kind': 'INICIAL', 'reference': 'BANK-1'},
            follow_redirects=False).status_code == 403
