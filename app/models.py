from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, ForeignKeyConstraint, Index, Integer, String, Text, UniqueConstraint
from .db import Base


def uid():
    return str(uuid4())


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SaasPlan(Base):
    __tablename__ = 'saas_plans'
    id = Column(String(36), primary_key=True, default=uid)
    code = Column(String(32), nullable=False, unique=True)
    name = Column(String(80), nullable=False)
    price_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default='MXN')
    max_distributors = Column(Integer, nullable=False)
    max_products = Column(Integer, nullable=False)
    max_orders_month = Column(Integer, nullable=False)
    active = Column(Boolean, nullable=False, default=True)
    __table_args__ = (CheckConstraint('price_cents >= 0'),)


class Tenant(Base):
    __tablename__ = 'tenants'
    id = Column(String(36), primary_key=True, default=uid)
    slug = Column(String(63), nullable=False, unique=True)
    name = Column(String(140), nullable=False)
    contact_email = Column(String(254), nullable=False)
    brand_name = Column(String(140), nullable=False, default='')
    theme = Column(String(20), nullable=False, default='indigo')
    active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=now)


class TenantSubscription(Base):
    __tablename__ = 'tenant_subscriptions'
    tenant_id = Column(String(36), ForeignKey('tenants.id'), primary_key=True)
    plan_id = Column(String(36), ForeignKey('saas_plans.id'), nullable=False)
    status = Column(String(20), nullable=False, default='TRIAL')
    start_at = Column(DateTime, nullable=False, default=now)
    ends_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=now)


class TenantCharge(Base):
    __tablename__ = 'tenant_charges'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False)
    plan_id = Column(String(36), ForeignKey('saas_plans.id'), nullable=False)
    period_key = Column(String(7), nullable=False)  # YYYY-MM
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False, default='MXN')
    payment_reference = Column(String(120), nullable=False)
    recorded_by = Column(String(36), ForeignKey('users.id'), nullable=False)
    recorded_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'period_key', name='uq_tenant_charge_period'), CheckConstraint('amount_cents >= 0'),)


class User(Base):
    __tablename__ = 'users'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(20), nullable=False, default='distribuidor')
    referral_code = Column(String(24), unique=True, nullable=True, index=True)
    active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'email', name='uq_user_tenant_email'),
                      UniqueConstraint('tenant_id', 'id', name='uq_user_tenant_id'),)


class Session(Base):
    __tablename__ = 'sessions'
    token_hash = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey('users.id'), nullable=False, index=True)
    csrf_token = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=now)


class Product(Base):
    __tablename__ = 'products'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(String(700), nullable=False, default='')
    price_cents = Column(Integer, nullable=True)
    currency = Column(String(3), nullable=False, default='USD')
    billing_interval = Column(String(16), nullable=True)
    commission_bps = Column(Integer, nullable=False, default=0)
    active = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (UniqueConstraint('tenant_id', 'id', name='uq_product_tenant_id'), CheckConstraint('commission_bps >= 0 AND commission_bps <= 10000'),)


class Customer(Base):
    __tablename__ = 'customers'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    distributor_id = Column(String(36), nullable=False, index=True)
    name = Column(String(120), nullable=False)
    email = Column(String(254), nullable=False)
    phone = Column(String(50), nullable=False, default='')
    created_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'distributor_id'], ['users.tenant_id', 'users.id'], name='fk_customer_tenant_user'),
                      UniqueConstraint('tenant_id', 'distributor_id', 'email', name='uq_customer_distributor_email'),
                      UniqueConstraint('tenant_id', 'id', name='uq_customer_tenant_id'))


class Order(Base):
    __tablename__ = 'orders'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    code = Column(String(24), nullable=False, unique=True, index=True)
    distributor_id = Column(String(36), nullable=False, index=True)
    customer_id = Column(String(36), nullable=False)
    product_id = Column(String(36), nullable=False)
    product_name = Column(String(120), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price_cents = Column(Integer, nullable=False)
    total_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    billing_interval = Column(String(16), nullable=False)
    commission_bps = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default='PENDIENTE')
    source = Column(String(20), nullable=False, default='panel')
    note = Column(Text, nullable=False, default='')
    paid_at = Column(DateTime, nullable=True)
    next_renewal_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=now)
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'distributor_id'], ['users.tenant_id', 'users.id'], name='fk_order_tenant_distributor'),
                      ForeignKeyConstraint(['tenant_id', 'customer_id'], ['customers.tenant_id', 'customers.id'], name='fk_order_tenant_customer'),
                      ForeignKeyConstraint(['tenant_id', 'product_id'], ['products.tenant_id', 'products.id'], name='fk_order_tenant_product'),
                      UniqueConstraint('tenant_id', 'id', name='uq_order_tenant_id'),
                      Index('idx_orders_tenant_distributor_created', 'tenant_id', 'distributor_id', 'created_at'),
                      CheckConstraint('quantity > 0 AND total_cents >= 0'),)


class Payment(Base):
    __tablename__ = 'payments'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    order_id = Column(String(36), nullable=False, index=True)
    reference = Column(String(120), nullable=False)
    kind = Column(String(12), nullable=False)
    period_key = Column(String(20), nullable=False)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    paid_at = Column(DateTime, nullable=False, default=now)
    recorded_by = Column(String(36), nullable=False)
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'order_id'], ['orders.tenant_id', 'orders.id'], name='fk_payment_tenant_order'),
                      ForeignKeyConstraint(['tenant_id', 'recorded_by'], ['users.tenant_id', 'users.id'], name='fk_payment_tenant_actor'),
                      UniqueConstraint('order_id', 'kind', 'period_key', name='uq_payment_period'),
                      UniqueConstraint('tenant_id', 'id', name='uq_payment_tenant_id'),)


class Commission(Base):
    __tablename__ = 'commissions'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=False, index=True)
    order_id = Column(String(36), nullable=False, index=True)
    payment_id = Column(String(36), nullable=False, unique=True)
    distributor_id = Column(String(36), nullable=False, index=True)
    amount_cents = Column(Integer, nullable=False)
    currency = Column(String(3), nullable=False)
    status = Column(String(16), nullable=False, default='PENDIENTE')
    created_at = Column(DateTime, nullable=False, default=now)
    paid_at = Column(DateTime, nullable=True)
    payout_reference = Column(String(120), nullable=True)
    __table_args__ = (ForeignKeyConstraint(['tenant_id', 'order_id'], ['orders.tenant_id', 'orders.id'], name='fk_commission_tenant_order'),
                      ForeignKeyConstraint(['tenant_id', 'payment_id'], ['payments.tenant_id', 'payments.id'], name='fk_commission_tenant_payment'),
                      ForeignKeyConstraint(['tenant_id', 'distributor_id'], ['users.tenant_id', 'users.id'], name='fk_commission_tenant_distributor'),)


class Audit(Base):
    __tablename__ = 'audits'
    id = Column(String(36), primary_key=True, default=uid)
    tenant_id = Column(String(36), ForeignKey('tenants.id'), nullable=True, index=True)
    actor_id = Column(String(36), nullable=False)
    action = Column(String(80), nullable=False)
    entity_type = Column(String(40), nullable=False)
    entity_id = Column(String(36), nullable=False)
    detail = Column(Text, nullable=False, default='')
    created_at = Column(DateTime, nullable=False, default=now)
