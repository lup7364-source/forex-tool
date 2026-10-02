"""
Evalúa cada punto marcado (manual o automático) contra el TP/SL calculado
a partir de las bandas de Bollinger de su propia vela, revisando las velas
futuras para ver si tocó primero el Take Profit (exitoso) o el Stop Loss
(fallido). Guarda el resultado en points.resultado.

Requiere:
    pip install supabase python-dotenv

Uso:
    python evaluar_resultados.py AUDUSD M15
"""

import sys
import os
from dotenv import load_dotenv
from supabase import create_client

MAX_VELAS_ADELANTE = 200


def cargar_cliente():
    load_dotenv()
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    return create_client(url, key)


def traer_velas(cliente, symbol, timeframe):
    todas = []
    desde = 0
    tamano_pagina = 1000
    while True:
        resp = (
            cliente.table("candles")
            .select("id, timestamp, open, high, low, close, variables")
            .eq("symbol", symbol)
            .eq("timeframe", timeframe)
            .order("timestamp")
            .range(desde, desde + tamano_pagina - 1)
            .execute()
        )
        lote = resp.data
        todas.extend(lote)
        if len(lote) < tamano_pagina:
            break
        desde += tamano_pagina
    return todas


def calcular_tp_sl(direccion, vela):
    v = vela["variables"]
    bb_up, bb_mid, bb_dw = v["BB_UP"], v["BB_MID"], v["BB_DW"]
    close = vela["close"]

    if direccion == "buy":
        tp = close - ((bb_dw - bb_mid) * 0.5)
        sl = close + ((bb_dw - bb_mid) * 0.5)
    else:
        tp = close - ((bb_up - bb_mid) * 0.5)
        sl = close + ((bb_up - bb_mid) * 0.5)

    return tp, sl


def simular_resultado(direccion, tp, sl, velas_futuras):
    for futura in velas_futuras:
        if direccion == "buy":
            toco_tp = futura["high"] >= tp
            toco_sl = futura["low"] <= sl
        else:
            toco_tp = futura["low"] <= tp
            toco_sl = futura["high"] >= sl

        if toco_tp and toco_sl:
            return None  # ambiguo, no se puede saber cuál fue primero
        if toco_tp:
            return "exitoso"
        if toco_sl:
            return "fallido"

    return "pendiente"  # aún no se resuelve dentro del rango revisado


def evaluar(symbol, timeframe):
    cliente = cargar_cliente()

    print("Trayendo velas...")
    velas = traer_velas(cliente, symbol, timeframe)
    indice_por_id = {v["id"]: i for i, v in enumerate(velas)}
    print(f"{len(velas)} velas cargadas.")

    print("Trayendo puntos marcados...")
    puntos = (
        cliente.table("points")
        .select("id, candle_id, direccion")
        .execute()
        .data
    )
    print(f"{len(puntos)} puntos encontrados.")

    actualizados = 0
    for punto in puntos:
        i = indice_por_id.get(punto["candle_id"])
        if i is None:
            continue  # la vela de ese punto no es de este symbol/timeframe

        vela = velas[i]
        tp, sl = calcular_tp_sl(punto["direccion"], vela)
        futuras = velas[i + 1 : i + 1 + MAX_VELAS_ADELANTE]
        resultado = simular_resultado(punto["direccion"], tp, sl, futuras)

        if resultado is None:
            continue  # ambiguo, se deja como estaba

        cliente.table("points").update({"resultado": resultado}).eq("id", punto["id"]).execute()
        actualizados += 1

    print(f"Listo. {actualizados} puntos actualizados.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python evaluar_resultados.py <SYMBOL> <TIMEFRAME>")
        sys.exit(1)

    evaluar(sys.argv[1], sys.argv[2])
