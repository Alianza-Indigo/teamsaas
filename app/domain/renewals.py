"""Ciclos de renovación anclados al día de la compra inicial.

El calendario no deriva por pasar de enero 31 a febrero 28: marzo vuelve al 31.
La programación no salta períodos vencidos cuando el cliente paga tarde.
"""
import calendar
from datetime import datetime


def next_date(date, interval, anchor_day=None):
    if interval not in ('MENSUAL', 'ANUAL'):
        raise ValueError('No existe periodo siguiente para una venta única.')
    anchor_day = anchor_day or date.day
    if not 1 <= anchor_day <= 31:
        raise ValueError('Día de anclaje inválido.')
    year = date.year + (1 if interval == 'ANUAL' else 0)
    month = date.month + (1 if interval == 'MENSUAL' else 0)
    if month > 12:
        year += 1
        month -= 12
    day = min(anchor_day, calendar.monthrange(year, month)[1])
    return date.replace(year=year, month=month, day=day)


def renewal_period(date):
    return date.strftime('%Y-%m')
