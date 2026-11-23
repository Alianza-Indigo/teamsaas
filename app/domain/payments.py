"""Libro de pagos verificados y comisiones atómicas, sin cobros automáticos."""
import re
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from ..models import Order, Payment, Commission, now
from .common import actor_for, audit, commission_cents
from .renewals import next_date, renewal_period


def record_payment(db, tenant_id, actor_id, order, reference, kind='INICIAL',
                   period_key=None, amount_cents=None):
    actor_for(db, tenant_id, actor_id, roles=('admin',))
    if not order or order.tenant_id != tenant_id:
        raise ValueError('Pedido no disponible en este espacio.')
    if kind not in ('INICIAL', 'RENOVACION'):
        raise ValueError('Tipo de pago inválido.')
    reference = reference.strip()
    if not 2 <= len(reference) <= 120:
        raise ValueError('La referencia del pago debe tener entre 2 y 120 caracteres.')
    # Bloqueo de la fila de pedido en PostgreSQL: serializa dos registros
    # simultáneos del mismo periodo. SQLite usa la restricción UNIQUE.
    current = db.scalar(select(Order).where(Order.id == order.id,
                                      Order.tenant_id == tenant_id).with_for_update())
    if not current or current.status == 'CANCELADO':
        raise ValueError('Pedido cancelado o no disponible.')
    if kind == 'INICIAL':
        period_key = 'INICIAL'
        if current.paid_at or current.status != 'CONFIRMADO':
            # Reintentos de la misma transacción son idempotentes.
            previous = db.scalar(select(Payment).where(Payment.tenant_id == tenant_id,
                         Payment.order_id == current.id, Payment.kind == kind,
                         Payment.period_key == period_key))
            if previous and previous.reference == reference:
                return previous, db.scalar(select(Commission).where(Commission.payment_id == previous.id,
                                                       Commission.tenant_id == tenant_id))
            raise ValueError('El pedido debe estar confirmado y sin pago previo.')
    else:
        if not current.paid_at or current.billing_interval == 'UNICO' or current.status not in ('PAGADO', 'EN_PROCESO', 'ACTIVO'):
            raise ValueError('Este pedido no admite renovaciones.')
        if not period_key or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period_key):
            raise ValueError('Periodo de renovación inválido.')
        expected_period = renewal_period(current.next_renewal_date) if current.next_renewal_date else None
        if period_key != expected_period:
            previous = db.scalar(select(Payment).where(Payment.tenant_id == tenant_id,
                         Payment.order_id == current.id, Payment.kind == kind,
                         Payment.period_key == period_key))
            if previous and previous.reference == reference:
                return previous, db.scalar(select(Commission).where(Commission.payment_id == previous.id,
                                                       Commission.tenant_id == tenant_id))
            raise ValueError(f'La renovación debe corresponder al siguiente periodo pendiente: {expected_period}.')
    total = current.total_cents if amount_cents is None else int(amount_cents)
    if total != current.total_cents:
        raise ValueError('El importe no coincide con el precio registrado del pedido.')
    previous = db.scalar(select(Payment).where(Payment.tenant_id == tenant_id,
                   Payment.order_id == current.id, Payment.kind == kind,
                   Payment.period_key == period_key))
    if previous:
        if previous.reference == reference:
            return previous, db.scalar(select(Commission).where(Commission.payment_id == previous.id,
                                                    Commission.tenant_id == tenant_id))
        raise ValueError('Ese periodo ya tiene un pago registrado.')
    payment = Payment(tenant_id=tenant_id, order_id=current.id, reference=reference,
                      kind=kind, period_key=period_key, amount_cents=total,
                      currency=current.currency, recorded_by=actor_id)
    db.add(payment)
    db.flush()
    commission = Commission(tenant_id=tenant_id, order_id=current.id, payment_id=payment.id,
                            distributor_id=current.distributor_id,
                            amount_cents=commission_cents(total, current.commission_bps), currency=current.currency)
    db.add(commission)
    db.flush()
    if kind == 'INICIAL':
        current.paid_at = now()
        current.status = 'PAGADO'
        if current.billing_interval != 'UNICO':
            current.next_renewal_date = next_date(current.paid_at, current.billing_interval)
    else:
        # Los periodos se avanzan desde la fecha programada, NUNCA desde hoy.
        # Se preserva el día de facturación original (ejemplo 31->28->31).
        current.next_renewal_date = next_date(current.next_renewal_date,
                                    current.billing_interval, anchor_day=current.paid_at.day)
    audit(db, tenant_id, actor_id, 'payment.verified', 'payment', payment.id,
          f'{kind} {period_key} {reference}')
    audit(db, tenant_id, actor_id, 'commission.generated', 'commission', commission.id, payment.id)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError('Ese periodo ya contiene un pago; operación no duplicada.') from exc
    return payment, commission
