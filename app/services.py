"""API de compatibilidad con la versión de Pedidos Índigo.

Los procesos transaccionales viven ahora en app.domain, separados del CRUD de
catálogos y de las rutas HTTP. Se mantienen estos nombres para migraciones e
integraciones que ya consumen el módulo.
"""
from .domain.common import (STATUSES, TRANSITIONS, INTERVALS, money,
                            commission_cents, normalize_email, valid_product)
from .domain.orders import make_order, change_order_status
from .domain.payments import record_payment
from .domain.renewals import next_date
from .domain.commissions import change_commission_status
