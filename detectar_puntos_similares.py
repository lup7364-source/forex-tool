"""
Detecta, en las velas de AUDUSD M15, los puntos que cumplen EXACTAMENTE la
misma condición que tu indicador de TradingView (V01.pine):

  BULL POINT = el precio cruza HACIA ARRIBA la banda inferior de Bollinger
               Y el RSI está subiendo (comparado con la vela anterior)

  BEAR POINT = el precio cruza HACIA ABAJO la banda superior de Bollinger
               Y el RSI está bajando (comparado con la vela anterior)

Qué hace con cada punto detectado:
  1. Lo GUARDA en la tabla `points` (tipo "auto", con su dirección buy/sell).
     Después evaluar_resultados.py lo marca como exitoso/fallido al revisar
     si el precio tocó primero el TP o el SL.
  2. Manda el aviso a Telegram, pero solo si la vela es reciente (para no
     llenarte de avisos por velas viejas).

Uso normal (lo hace el workflow cada 15 min): revisa las velas con
notificado = false.
    python detectar_puntos_similares.py

Uso único para rellenar TODO el historial (marca los puntos de todas las
velas guardadas que aún no estén en `points`, sin mandar avisos):
    python detectar_puntos_similares.py --reprocesar

Antes de usarlo, corre UNA VEZ en el SQL Editor de Supabase (por si la
columna "tipo" de points solo acepta bueno/malo):
    alter table points drop constraint if exists points_tipo_check;
    alter table candles add column if not exists notificado boolean default false;

Requiere:
    pip install requests python-dotenv

Variables de entorno (además de las de Supabase):
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

import supabase_rest as db

SYMBOL = "AUDUSD"
TIMEFRAME = "M15"

TIPO_PUNTO_AUTOMATICO = "auto"
EDAD_MAXIMA_AVISO_MIN = 90  # solo se avisa por Telegram si la vela tiene menos de esto
HORAS_BROKER_RESPECTO_A_UTC = 3

COLUMNAS_VELA = "id,timestamp,close,notificado,variables"


def ahora_en_broker():
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=HORAS_BROKER_RESPECTO_A_UTC)


def parsear_timestamp(valor):
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


def enviar_telegram(texto):
    """Devuelve True si el mensaje salió bien."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    try:
        respuesta = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": texto},
            timeout=15,
        )
    except requests.RequestException as error:
        print(f"Aviso: no se pudo enviar el mensaje a Telegram: {error}")
        return False

    if respuesta.status_code >= 300:
        print(f"Aviso: no se pudo enviar el mensaje a Telegram: {respuesta.text}")
        return False
    return True


def detectar_punto(vela_actual, vela_anterior):
    """Replica exacta de bull_point / bear_point del indicador V01.pine."""
    if vela_anterior is None:
        return None

    v_now = vela_actual["variables"] or {}
    v_prev = vela_anterior["variables"] or {}

    close_now, close_prev = vela_actual["close"], vela_anterior["close"]
    bb_dw_now, bb_dw_prev = v_now.get("BB_DW"), v_prev.get("BB_DW")
    bb_up_now, bb_up_prev = v_now.get("BB_UP"), v_prev.get("BB_UP")
    bb_mid_now = v_now.get("BB_MID")
    rsi_now, rsi_prev = v_now.get("RSI"), v_prev.get("RSI")

    if None in (close_now, close_prev, bb_dw_now, bb_dw_prev, bb_up_now, bb_up_prev, bb_mid_now, rsi_now, rsi_prev):
        return None

    cruza_sobre_banda_inferior = close_prev <= bb_dw_prev and close_now > bb_dw_now
    cruza_bajo_banda_superior = close_prev >= bb_up_prev and close_now < bb_up_now
    rsi_sube = rsi_prev < rsi_now
    rsi_baja = rsi_prev > rsi_now

    if cruza_sobre_banda_inferior and rsi_sube:
        tp = close_now - ((bb_dw_now - bb_mid_now) * 0.5)
        sl = close_now + ((bb_dw_now - bb_mid_now) * 0.5)
        return {"tipo": "BULL POINT", "direccion": "buy", "tp": tp, "sl": sl, "rsi": rsi_now}

    if cruza_bajo_banda_superior and rsi_baja:
        tp = close_now - ((bb_up_now - bb_mid_now) * 0.5)
        sl = close_now + ((bb_up_now - bb_mid_now) * 0.5)
        return {"tipo": "BEAR POINT", "direccion": "sell", "tp": tp, "sl": sl, "rsi": rsi_now}

    return None


def fila_punto(vela, punto):
    return {
        "candle_id": vela["id"],
        "tipo": TIPO_PUNTO_AUTOMATICO,
        "direccion": punto["direccion"],
        "nota": f"AUTO · {punto['tipo']} (V01)",
    }


def traer_puntos_existentes():
    """Conjunto de (candle_id, direccion) que ya están en points (manuales o automáticos)."""
    filas = db.seleccionar_todo("points", {"select": "id,candle_id,direccion", "order": "id.asc"})
    return {(p["candle_id"], p["direccion"]) for p in filas}


def pip_size():
    # Pares con JPY: 1 pip = 0.01. El resto (AUDUSD, etc.): 0.0001
    return 0.01 if "JPY" in SYMBOL.upper() else 0.0001


def texto_aviso(vela, punto):
    emoji = "🟢" if punto["direccion"] == "buy" else "🔴"
    pip = pip_size()
    etp = abs(vela["close"] - punto["tp"]) / pip  # expected TP en pips
    esl = abs(vela["close"] - punto["sl"]) / pip  # expected SL en pips
    fecha = str(vela["timestamp"])[:10]  # solo AAAA-MM-DD

    return (
        f"{emoji} {punto['tipo']} detectado\n"
        f"{SYMBOL} {TIMEFRAME} — {fecha}\n"
        f"Entry: {vela['close']:.5f}\n"
        f"TP: {punto['tp']:.5f}\n"
        f"SL: {punto['sl']:.5f}\n"
        f"ETP: {etp:.1f} pips\n"
        f"ESL: {esl:.1f} pips\n"
        f"RSI: {punto['rsi']:.2f}"
    )


def filtros_serie():
    return {"symbol": f"eq.{SYMBOL}", "timeframe": f"eq.{TIMEFRAME}"}


def reprocesar_historial():
    """Marca como punto TODA vela guardada que cumpla la condición y aún no esté en points."""
    print("Trayendo todas las velas guardadas...")
    velas = db.seleccionar_todo(
        "candles", {"select": COLUMNAS_VELA, **filtros_serie(), "order": "timestamp.asc"}
    )
    print(f"{len(velas)} velas cargadas.")

    existentes = traer_puntos_existentes()
    filas = []

    for i in range(1, len(velas)):
        punto = detectar_punto(velas[i], velas[i - 1])
        if not punto:
            continue
        clave = (velas[i]["id"], punto["direccion"])
        if clave in existentes:
            continue
        filas.append(fila_punto(velas[i], punto))
        existentes.add(clave)

    db.insertar("points", filas)
    print(f"Listo. {len(filas)} punto(s) nuevo(s) guardado(s) en points (sin avisos de Telegram).")


def revisar_velas_nuevas():
    print("Buscando velas sin revisar...")
    pendientes = db.seleccionar_todo(
        "candles",
        {
            "select": "id,timestamp",
            **filtros_serie(),
            "or": "(notificado.is.false,notificado.is.null)",
            "order": "timestamp.asc",
        },
    )

    if not pendientes:
        print("No hay velas nuevas por revisar.")
        return True

    print(f"{len(pendientes)} vela(s) por revisar.")
    primer_ts = pendientes[0]["timestamp"]

    # La vela anterior a la primera pendiente + todas desde ahí: así cada vela
    # pendiente tiene a su lado la vela previa real de la base de datos.
    previa = db.seleccionar(
        "candles",
        {
            "select": COLUMNAS_VELA,
            **filtros_serie(),
            "timestamp": f"lt.{primer_ts}",
            "order": "timestamp.desc",
            "limit": 1,
        },
    )
    ventana = db.seleccionar_todo(
        "candles",
        {
            "select": COLUMNAS_VELA,
            **filtros_serie(),
            "timestamp": f"gte.{primer_ts}",
            "order": "timestamp.asc",
        },
    )
    velas = previa + ventana

    existentes = traer_puntos_existentes()
    ahora = ahora_en_broker()
    marcar_revisadas = []
    todo_bien = True

    for i, vela in enumerate(velas):
        if vela.get("notificado"):
            continue  # ya revisada antes (solo está en la lista como contexto)

        anterior = velas[i - 1] if i > 0 else None
        punto = detectar_punto(vela, anterior)
        aviso_ok = True

        if punto:
            clave = (vela["id"], punto["direccion"])
            if clave not in existentes:
                try:
                    db.insertar("points", [fila_punto(vela, punto)])
                    existentes.add(clave)
                    print(f"Punto guardado: {punto['tipo']} en {vela['timestamp']}")
                except Exception as error:  # el aviso de Telegram es lo prioritario: se sigue
                    print(f"ERROR guardando el punto de {vela['timestamp']}: {error}")
                    todo_bien = False

            edad = ahora - parsear_timestamp(vela["timestamp"])
            if edad <= timedelta(minutes=EDAD_MAXIMA_AVISO_MIN):
                aviso_ok = enviar_telegram(texto_aviso(vela, punto))
                if aviso_ok:
                    print(f"Aviso enviado: {punto['tipo']} en {vela['timestamp']}")
            else:
                print(f"{punto['tipo']} en {vela['timestamp']} es una vela vieja: se guarda sin aviso.")

        if aviso_ok:
            marcar_revisadas.append(vela["id"])

    db.actualizar_por_ids("candles", marcar_revisadas, {"notificado": True})
    print("Listo.")
    return todo_bien


def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reprocesar",
        action="store_true",
        help="Marca en points todos los puntos del historial completo (sin avisos).",
    )
    args = parser.parse_args()

    if args.reprocesar:
        reprocesar_historial()
        return

    if not revisar_velas_nuevas():
        sys.exit(1)  # que el workflow quede en rojo y te enteres


if __name__ == "__main__":
    main()
