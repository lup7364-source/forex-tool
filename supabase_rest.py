"""
Acceso mínimo a la API REST de Supabase usando solo `requests`.

Se usa en lugar de la librería supabase-py porque con la service_role key
esa librería daba errores de RLS al escribir (subir_velas.py ya tuvo que
reescribirse por lo mismo). Todos los scripts del workflow usan este módulo.

Variables de entorno necesarias:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

TIMEOUT = 60
TAM_PAGINA = 1000  # Supabase devuelve como máximo 1000 filas por consulta


def _url_base():
    return os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1"


def _headers(extra=None):
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def _revisar(respuesta, accion):
    if respuesta.status_code >= 300:
        raise RuntimeError(f"{accion}: HTTP {respuesta.status_code} — {respuesta.text}")


def seleccionar(tabla, params):
    """Una sola consulta (sin paginar). `params` usa la sintaxis de PostgREST,
    por ejemplo {"select": "id,close", "symbol": "eq.AUDUSD", "limit": 1}."""
    respuesta = requests.get(
        f"{_url_base()}/{tabla}", headers=_headers(), params=params, timeout=TIMEOUT
    )
    _revisar(respuesta, f"Leyendo {tabla}")
    return respuesta.json()


def seleccionar_todo(tabla, params):
    """Trae TODAS las filas, página por página (para saltar el límite de 1000)."""
    params = dict(params)
    params.setdefault("order", "id.asc")  # el orden debe ser estable para paginar

    filas = []
    desde = 0
    while True:
        pagina = seleccionar(tabla, {**params, "limit": TAM_PAGINA, "offset": desde})
        if not pagina:
            break
        filas.extend(pagina)
        desde += len(pagina)
    return filas


def insertar(tabla, filas, on_conflict=None, tam_lote=500):
    """Inserta filas. Con `on_conflict` hace upsert (actualiza si ya existe)."""
    if not filas:
        return

    params = {"on_conflict": on_conflict} if on_conflict else None
    prefer = "return=minimal"
    if on_conflict:
        prefer += ",resolution=merge-duplicates"

    for i in range(0, len(filas), tam_lote):
        respuesta = requests.post(
            f"{_url_base()}/{tabla}",
            headers=_headers({"Prefer": prefer}),
            params=params,
            json=filas[i : i + tam_lote],
            timeout=TIMEOUT,
        )
        _revisar(respuesta, f"Escribiendo en {tabla}")


def actualizar_por_ids(tabla, ids, valores, tam_lote=100):
    """UPDATE ... SET valores WHERE id IN (ids), en lotes."""
    ids = list(ids)
    for i in range(0, len(ids), tam_lote):
        lote = ids[i : i + tam_lote]
        respuesta = requests.patch(
            f"{_url_base()}/{tabla}",
            headers=_headers({"Prefer": "return=minimal"}),
            params={"id": "in.(" + ",".join(str(x) for x in lote) + ")"},
            json=valores,
            timeout=TIMEOUT,
        )
        _revisar(respuesta, f"Actualizando {tabla}")
