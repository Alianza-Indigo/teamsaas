"""Import a legacy Pedidos Índigo SQLite database into a new SaaS database.

Usage: DATABASE_URL=postgresql+psycopg://... python -m scripts.import_legacy_sqlite --legacy /path/pedidos_indigo.db
Target schema MUST already be initialized with `python -m app.bootstrap`.
Does not modify the legacy source. No credentials/sessions are exported to a file.
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path
from sqlalchemy import select, func
from app.db import SessionLocal
from app.models import Audit, Commission, Customer, Order, Payment, Product, Tenant, User


def parse_date(value):
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def import_data(source):
    path = Path(source).resolve(strict=True)
    if path.suffix.lower() not in ('.sqlite', '.sqlite3', '.db'):
        raise ValueError('Se requiere un archivo de base de datos SQLite.')
    connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        source_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        needed = {'users', 'products', 'customers', 'orders', 'payments', 'commissions', 'audits'}
        if not needed.issubset(source_tables):
            raise ValueError('La base de origen no tiene el esquema anterior esperado.')
        all_rows = {table: [dict(r) for r in connection.execute(f'SELECT * FROM {table}')] for table in needed}
        with SessionLocal() as db:
            tenant = db.scalar(select(Tenant).where(Tenant.slug == 'team-indigo'))
            if not tenant:
                raise RuntimeError('Ejecuta app.bootstrap en una base de destino nueva primero.')
            for cls in (Order, Payment, Commission, Customer):
                if db.scalar(select(func.count()).select_from(cls).where(cls.tenant_id == tenant.id)):
                    raise RuntimeError('La importación exige una cuenta Team Índigo sin actividad, para prevenir duplicados.')
            # Every legacy user is assigned to Team Índigo; existing bootstrap owner is reused by email.
            users = {}
            for row in all_rows['users']:
                user = db.scalar(select(User).where(User.tenant_id == tenant.id, User.email == row['email']))
                if user:
                    if user.role != row['role']:
                        raise RuntimeError(f'Conflicto de roles en cuenta existente: {row["email"]}')
                    users[row['id']] = user.id
                else:
                    user = User(id=row['id'], tenant_id=tenant.id, name=row['name'], email=row['email'], password_hash=row['password_hash'],
                                role=row['role'], referral_code=row['referral_code'], active=bool(row['active']),
                                must_change_password=bool(row['must_change_password']), created_at=parse_date(row['created_at']))
                    db.add(user)
                    users[row['id']] = user.id
            db.flush()
            # Remove unused placeholder product rows that duplicate the legacy catalog.
            for row in all_rows['products']:
                old = db.scalar(select(Product).where(Product.tenant_id == tenant.id, Product.name == row['name']))
                if old and not old.active:
                    db.delete(old)
            db.flush()
            for row in all_rows['products']:
                db.add(Product(id=row['id'], tenant_id=tenant.id, name=row['name'], description=row['description'],
                               price_cents=row['price_cents'], currency=row['currency'], billing_interval=row['billing_interval'],
                               commission_bps=row['commission_bps'], active=bool(row['active']), created_at=parse_date(row['created_at'])))
            db.flush()
            for row in all_rows['customers']:
                db.add(Customer(id=row['id'], tenant_id=tenant.id, distributor_id=users[row['distributor_id']],
                                name=row['name'], email=row['email'], phone=row['phone'], created_at=parse_date(row['created_at'])))
            db.flush()
            for row in all_rows['orders']:
                db.add(Order(id=row['id'], tenant_id=tenant.id, code=row['code'], distributor_id=users[row['distributor_id']],
                             customer_id=row['customer_id'], product_id=row['product_id'], product_name=row['product_name'],
                             quantity=row['quantity'], unit_price_cents=row['unit_price_cents'], total_cents=row['total_cents'],
                             currency=row['currency'], billing_interval=row['billing_interval'], commission_bps=row['commission_bps'],
                             status=row['status'], source=row['source'], note=row['note'], paid_at=parse_date(row['paid_at']),
                             next_renewal_date=parse_date(row['next_renewal_date']), created_at=parse_date(row['created_at'])))
            db.flush()
            for row in all_rows['payments']:
                db.add(Payment(id=row['id'], tenant_id=tenant.id, order_id=row['order_id'], reference=row['reference'],
                               kind=row['kind'], period_key=row['period_key'], amount_cents=row['amount_cents'],
                               currency=row['currency'], paid_at=parse_date(row['paid_at']), recorded_by=users[row['recorded_by']]))
            db.flush()
            for row in all_rows['commissions']:
                db.add(Commission(id=row['id'], tenant_id=tenant.id, order_id=row['order_id'], payment_id=row['payment_id'],
                                  distributor_id=users[row['distributor_id']], amount_cents=row['amount_cents'],
                                  currency=row['currency'], status=row['status'], created_at=parse_date(row['created_at']),
                                  paid_at=parse_date(row['paid_at']), payout_reference=row['payout_reference']))
            for row in all_rows['audits']:
                db.add(Audit(id=row['id'], tenant_id=tenant.id, actor_id=users.get(row['actor_id'], row['actor_id']),
                             action=row['action'], entity_type=row['entity_type'], entity_id=row['entity_id'],
                             detail=row['detail'], created_at=parse_date(row['created_at'])))
            db.commit()
            return {k: len(v) for k, v in all_rows.items()}
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--legacy', required=True, help='Archivo SQLite original (solo lectura)')
    args = parser.parse_args()
    print('Importación completada:', import_data(args.legacy))
