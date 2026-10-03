"""
Evalúa cada punto marcado (manual o automático) contra el TP/SL calculado
a partir de las bandas de Bollinger de su propia vela, revisando las velas
futuras para ver si tocó primero el Take Profit (exitoso) o el Stop Loss
(fallido). Guarda el resultado en points.resultado.

Solo revisa los puntos que todavía no tienen resultado (o están "pendiente"),
y solo trae las velas desde el punto más antiguo por resolver.

Requiere:
    pip install requests python-dotenv

Uso:
    python evaluar_resultados.py AUDUSD M15
"""

import sys

import supabase_rest as db

MAX_VELAS_ADELANTE = 200


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
    print("Trayendo puntos sin resolver...")
    puntos = db.seleccionar_todo(
        "points",
        {
            "select": "id,candle_id,direccion,resultado,candles(id,symbol,timeframe,timestamp)",
            "or": "(resultado.is.null,resultado.eq.pendiente)",
            "order": "id.asc",
        },
    )
    puntos = [
        p for p in puntos
        if p.get("candles")
        and p["candles"]["symbol"] == symbol
        and p["candles"]["timeframe"] == timeframe
    ]
    print(f"{len(puntos)} punto(s) por evaluar.")
    if not puntos:
        return

    desde = min(p["candles"]["timestamp"] for p in puntos)

    print("Trayendo velas...")
    velas = db.seleccionar_todo(
        "candles",
        {
            "select": "id,timestamp,open,high,low,close,variables",
            "symbol": f"eq.{symbol}",
            "timeframe": f"eq.{timeframe}",
            "timestamp": f"gte.{desde}",
            "order": "timestamp.asc",
        },
    )
    indice_por_id = {v["id"]: i for i, v in enumerate(velas)}
    print(f"{len(velas)} velas cargadas.")

    ids_por_resultado = {"exitoso": [], "fallido": []}

    for punto in puntos:
        i = indice_por_id.get(punto["candle_id"])
        if i is None:
            continue

        vela = velas[i]
        if not vela.get("variables") or "BB_UP" not in vela["variables"]:
            continue

        tp, sl = calcular_tp_sl(punto["direccion"], vela)
        futuras = velas[i + 1 : i + 1 + MAX_VELAS_ADELANTE]
        resultado = simular_resultado(punto["direccion"], tp, sl, futuras)

        if resultado in ids_por_resultado:
            ids_por_resultado[resultado].append(punto["id"])
        # "pendiente" y ambiguo (None) se dejan como estaban: se reintentan en la próxima corrida

    for resultado, ids in ids_por_resultado.items():
        db.actualizar_por_ids("points", ids, {"resultado": resultado})

    print(
        f"Listo. {len(ids_por_resultado['exitoso'])} exitoso(s), "
        f"{len(ids_por_resultado['fallido'])} fallido(s) nuevos."
    )


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Uso: python evaluar_resultados.py <SYMBOL> <TIMEFRAME>")
        sys.exit(1)

    evaluar(sys.argv[1], sys.argv[2])
