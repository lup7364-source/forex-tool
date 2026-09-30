"""
Revisa las velas nuevas de AUDUSD M15 y detecta si cumplen EXACTAMENTE la
misma condición que tu indicador de TradingView (V01.pine):

  BULL POINT = el precio cruza HACIA ARRIBA la banda inferior de Bollinger
               Y el RSI está subiendo (comparado con la vela anterior)

  BEAR POINT = el precio cruza HACIA ABAJO la banda superior de Bollinger
               Y el RSI está bajando (comparado con la vela anterior)

En cuanto detecta una de las dos, manda el aviso a Telegram al instante
(no espera a saber si a futuro seria exitoso o fallido).

Antes de usarlo, corre UNA VEZ en el SQL Editor de Supabase:
    alter table candles add column if not exists notificado boolean default false;

Requiere:
    pip install supabase python-dotenv requests

Variables de entorno (además de las que ya tienes):
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...

Uso:
    python detectar_puntos_similares.py
"""

import os

import requests
from dotenv import load_dotenv
from supabase import create_client

SYMBOL = "AUDUSD"
TIMEFRAME = "M15"


def cargar_config():
    load_dotenv()
    return {
        "supabase_url": os.environ["SUPABASE_URL"],
        "supabase_key": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "telegram_token": os.environ["TELEGRAM_BOT_TOKEN"],
        "telegram_chat_id": os.environ["TELEGRAM_CHAT_ID"],
    }


def enviar_telegram(token, chat_id, texto):
    respuesta = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": texto},
        timeout=15,
    )
    if respuesta.status_code >= 300:
        print(f"Aviso: no se pudo enviar el mensaje a Telegram: {respuesta.text}")


def traer_vela_anterior(cliente, antes_de_timestamp):
    resp = (
        cliente.table("candles")
        .select("timestamp, close, variables")
        .eq("symbol", SYMBOL)
        .eq("timeframe", TIMEFRAME)
        .lt("timestamp", antes_de_timestamp)
        .order("timestamp", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


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


def main():
    config = cargar_config()
    cliente = create_client(config["supabase_url"], config["supabase_key"])

    print("Buscando velas sin revisar...")
    resp = (
        cliente.table("candles")
        .select("id, timestamp, close, variables")
        .eq("symbol", SYMBOL)
        .eq("timeframe", TIMEFRAME)
        .eq("notificado", False)
        .order("timestamp")
        .execute()
    )
    velas_nuevas = resp.data

    if not velas_nuevas:
        print("No hay velas nuevas por revisar.")
        return

    print(f"{len(velas_nuevas)} vela(s) por revisar.")

    for vela in velas_nuevas:
        anterior = traer_vela_anterior(cliente, vela["timestamp"])
        punto = detectar_punto(vela, anterior)

        if punto:
            emoji = "🟢" if punto["direccion"] == "buy" else "🔴"
            texto = (
                f"{emoji} {punto['tipo']} detectado\n"
                f"{SYMBOL} {TIMEFRAME} — {vela['timestamp']}\n"
                f"Entry: {vela['close']:.5f}\n"
                f"TP: {punto['tp']:.5f}\n"
                f"SL: {punto['sl']:.5f}\n"
                f"RSI: {punto['rsi']:.2f}"
            )
            enviar_telegram(config["telegram_token"], config["telegram_chat_id"], texto)
            print(f"Aviso enviado: {punto['tipo']} en {vela['timestamp']}")

        cliente.table("candles").update({"notificado": True}).eq("id", vela["id"]).execute()

    print("Listo.")


if __name__ == "__main__":
    main()
