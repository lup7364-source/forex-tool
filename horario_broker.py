"""
Conversión entre UTC y la hora del servidor de tu bróker (MT4).

Muchos brókers de MT4 usan UTC+2 en invierno y UTC+3 en verano, siguiendo el
cambio de hora de EE. UU.:
  * Verano (UTC+3): desde el 2.º domingo de marzo hasta el 1.er domingo de noviembre.
  * Invierno (UTC+2): el resto del año.

El cambio se hace el domingo, con el mercado cerrado, así que no afecta velas.

Si algún día tu bróker no sigue esta regla, solo hay que cambiar este archivo.
"""

from datetime import datetime, timedelta

OFFSET_VERANO = 3
OFFSET_INVIERNO = 2


def _domingo_numero(anio, mes, n):
    """Fecha del n-ésimo domingo del mes (n = 1 es el primero)."""
    primero = datetime(anio, mes, 1)
    dias_hasta_domingo = (6 - primero.weekday()) % 7  # lunes = 0 ... domingo = 6
    return primero + timedelta(days=dias_hasta_domingo + 7 * (n - 1))


def offset_broker_horas(utc_naive):
    """Horas que hay que sumar a un datetime UTC (sin zona) para tener la hora del bróker."""
    anio = utc_naive.year
    inicio_verano = _domingo_numero(anio, 3, 2).replace(hour=7)  # 2:00 EST = 07:00 UTC
    fin_verano = _domingo_numero(anio, 11, 1).replace(hour=6)    # 2:00 EDT = 06:00 UTC
    return OFFSET_VERANO if inicio_verano <= utc_naive < fin_verano else OFFSET_INVIERNO


def utc_a_broker(utc_naive):
    """Convierte un datetime UTC (sin zona horaria) a la hora del bróker (también sin zona)."""
    return utc_naive + timedelta(hours=offset_broker_horas(utc_naive))
