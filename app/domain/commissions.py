"""Aprobación y liquidación de comisiones con máquina de estados."""
from sqlalchemy import select
from ..models import Commission, now
from .common import actor_for, audit


def change_commission_status(db, tenant_id, actor_id, commission, to_status, payout_reference=None):
    actor_for(db, tenant_id, actor_id, roles=('admin',))
    if not commission or commission.tenant_id != tenant_id:
        raise ValueError('Comisión no disponible en este espacio.')
    current = db.scalar(select(Commission).where(Commission.id == commission.id,
                                    Commission.tenant_id == tenant_id).with_for_update())
    allowed = {'PENDIENTE': 'APROBADA', 'APROBADA': 'PAGADA'}
    if allowed.get(current.status) != to_status:
        raise ValueError('Transición de comisión no permitida.')
    if to_status == 'PAGADA':
        payout_reference = (payout_reference or '').strip()
        if not 2 <= len(payout_reference) <= 120:
            raise ValueError('Es obligatorio registrar una referencia de liquidación de 2 a 120 caracteres.')
        current.paid_at = now()
        current.payout_reference = payout_reference
    current.status = to_status
    audit(db, tenant_id, actor_id, 'commission.status', 'commission', current.id,
          f'{to_status} / {current.payout_reference or ""}')
    db.commit()
