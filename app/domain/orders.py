"""Captura de pedidos y máquina de estados; sin operaciones CRUD directas de pagos."""
import secrets
from sqlalchemy import select
from ..models import Customer, Order, Product, User
from ..saas import ensure_quota
from .common import TRANSITIONS, actor_for, audit, normalize_email, valid_product


def make_order(db, tenant_id, actor_id, distributor_id, product_id, customer_name,
               customer_email, phone, quantity, note='', source='panel'):
    actor = actor_for(db, tenant_id, actor_id)
    if source not in ('panel', 'enlace'):
        raise ValueError('Origen de solicitud inválido.')
    if actor.role == 'distribuidor' and actor.id != distributor_id:
        raise ValueError('El distribuidor no puede registrar ventas de otro vendedor.')
    ensure_quota(db, tenant_id, 'orders')
    distributor = db.get(User, distributor_id)
    product = db.get(Product, product_id)
    if not distributor or distributor.tenant_id != tenant_id or not distributor.active or distributor.role not in ('distribuidor', 'admin') or (distributor.role == 'admin' and actor.id != distributor.id):
        raise ValueError('Distribuidor no disponible en este espacio.')
    if not product or product.tenant_id != tenant_id or not valid_product(product):
        raise ValueError('Producto no disponible en este espacio.')
    customer_email = normalize_email(customer_email)
    customer_name = customer_name.strip()
    # Los distribuidores solo necesitan el correo. El nombre puede
    # completarse después, sin inventar datos personales.
    if not customer_name and actor.role == 'distribuidor':
        customer_name = 'Cliente'
    if not 2 <= len(customer_name) <= 120:
        raise ValueError('Indica el nombre del cliente (2 a 120 caracteres).')
    try:
        quantity = int(quantity)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('Cantidad inválida.') from exc
    if not 1 <= quantity <= 500:
        raise ValueError('La cantidad debe estar entre 1 y 500.')
    if len(phone) > 50 or len(note) > 2000:
        raise ValueError('Los datos exceden el máximo permitido.')
    # El cliente pertenece al distribuidor dentro de la misma empresa.
    customer = db.scalar(select(Customer).where(Customer.tenant_id == tenant_id,
                    Customer.distributor_id == distributor_id, Customer.email == customer_email))
    if customer is None:
        customer = Customer(tenant_id=tenant_id, distributor_id=distributor_id,
                            name=customer_name, email=customer_email, phone=phone.strip())
        db.add(customer)
        db.flush()
    else:
        if customer_name != 'Cliente' or not customer.name:
            customer.name = customer_name
        if phone.strip():
            customer.phone = phone.strip()
    # El importe y la comisión se congelan: cambios futuros al catálogo NO
    # alteran operaciones históricas ni las comisiones recurrentes asociadas.
    order = Order(tenant_id=tenant_id, code='TI-' + secrets.token_hex(8).upper(),
                  distributor_id=distributor_id, customer_id=customer.id,
                  product_id=product.id, product_name=product.name, quantity=quantity,
                  unit_price_cents=product.price_cents,
                  total_cents=quantity * product.price_cents, currency=product.currency,
                  billing_interval=product.billing_interval, commission_bps=0 if distributor.role == 'admin' else product.commission_bps,
                  note=note.strip(), source=source)
    db.add(order)
    db.flush()
    audit(db, tenant_id, actor_id, 'order.created', 'order', order.id, source)
    db.commit()
    return order


def change_order_status(db, tenant_id, actor_id, order, new_status):
    actor_for(db, tenant_id, actor_id, roles=('admin',))
    if not order or order.tenant_id != tenant_id:
        raise ValueError('Pedido no disponible en este espacio.')
    if new_status not in TRANSITIONS.get(order.status, set()):
        raise ValueError('Transición de estado no permitida.')
    previous = order.status
    order.status = new_status
    audit(db, tenant_id, actor_id, 'order.status', 'order', order.id, f'{previous} -> {new_status}')
    db.commit()
