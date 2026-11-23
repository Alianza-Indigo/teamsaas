import pytest
from sqlalchemy import select
from app.db import SessionLocal
from app.models import Order, TenantSubscription, Product
from app.services import commission_cents, make_order, change_order_status, record_payment, change_commission_status
from app.saas import ensure_quota


def test_commission_rounding():
    assert commission_cents(100, 1250) == 13
    assert commission_cents(7500, 2000) == 1500


def test_wrong_tenant_product_rejected():
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            make_order(db, 'tenant-a', 'dist-a', 'dist-a', 'prod-b', 'Cliente', 'x@example.com', '', 1)
        assert not db.scalars(select(Order)).all()


def test_wrong_tenant_distributor_rejected():
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            make_order(db, 'tenant-a', 'admin-a', 'dist-b', 'prod-a', 'Cliente', 'x@example.com', '', 1)


def test_payment_and_commission_tenant_checks():
    with SessionLocal() as db:
        order = make_order(db, 'tenant-a', 'dist-a', 'dist-a', 'prod-a', 'Cliente', 'x@example.com', '', 1)
        with pytest.raises(ValueError):
            change_order_status(db, 'tenant-b', 'admin-b', order, 'CONFIRMADO')
        change_order_status(db, 'tenant-a', 'admin-a', order, 'CONFIRMADO')
        with pytest.raises(ValueError):
            record_payment(db, 'tenant-b', 'admin-b', order, 'ref1')
        _, commission = record_payment(db, 'tenant-a', 'admin-a', order, 'ref1')
        assert commission.amount_cents == 1500
        with pytest.raises(ValueError):
            change_commission_status(db, 'tenant-b', 'admin-b', commission, 'APROBADA')
        change_commission_status(db, 'tenant-a', 'admin-a', commission, 'APROBADA')
        change_commission_status(db, 'tenant-a', 'admin-a', commission, 'PAGADA', payout_reference='transfer-123')
        assert commission.status == 'PAGADA'


def test_renewals_cannot_repeat():
    with SessionLocal() as db:
        order = make_order(db, 'tenant-a', 'dist-a', 'dist-a', 'prod-a', 'Cliente', 'x@example.com', '', 1)
        change_order_status(db, 'tenant-a', 'admin-a', order, 'CONFIRMADO')
        record_payment(db, 'tenant-a', 'admin-a', order, 'init')
        with pytest.raises(ValueError):
            record_payment(db, 'tenant-a', 'admin-a', order, 'duplicate')
        period = order.next_renewal_date.strftime('%Y-%m')
        record_payment(db, 'tenant-a', 'admin-a', order, 'renewal', kind='RENOVACION', period_key=period)
        with pytest.raises(ValueError):
            record_payment(db, 'tenant-a', 'admin-a', order, 'dup-renewal', kind='RENOVACION', period_key=period)


def test_suspended_subscription_blocks_order():
    with SessionLocal() as db:
        sub = db.get(TenantSubscription, 'tenant-a')
        sub.status = 'SUSPENDIDA'
        db.commit()
        with pytest.raises(ValueError):
            make_order(db, 'tenant-a', 'dist-a', 'dist-a', 'prod-a', 'Cliente', 'x@example.com', '', 1)
