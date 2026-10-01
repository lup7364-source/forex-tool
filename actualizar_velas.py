"""
Trae las velas nuevas de AUDUSD M15 desde Twelve Data y las sube a Supabase,
calculando los indicadores en continuidad con el historial que ya tienes
(ATR, EMA_21 y SMMA_21/50/200 no se reinician: siguen la misma fórmula
recursiva a partir del último valor real que ya calculó MT4).

Si la última vela guardada es de hace días, esta primera corrida rellena
automáticamente todo el hueco hasta ahora (no hace falta un script aparte
para el backfill: es el mismo).

Requiere:
    pip install supabase python-dotenv requests

Variables de entorno (archivo .env):
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
    TWELVE_DATA_API_KEY=...

Uso:
    python actualizar_velas.py
"""

import os
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv
from supabase import create_client

SYMBOL = "AUDUSD"
TIMEFRAME = "M15"
TWELVE_DATA_SYMBOL = "AUD/USD"
TWELVE_DATA_INTERVAL = "15min"

HISTORIAL_PARA_CONTEXTO = 250  # velas previas que se traen de Supabase, para
                                # dar contexto a RSI/MACD (no para reiniciar
                                # SMMA/EMA/ATR: esos continúan del último valor real)


def cargar_config():
    load_dotenv()
    return {
        "supabase_url": os.environ["SUPABASE_URL"],
        "supabase_key": os.environ["SUPABASE_SERVICE_ROLE_KEY"],
        "twelve_data_key": os.environ["TWELVE_DATA_API_KEY"],
    }


def traer_ultimas_velas_supabase(cliente, cuantas=HISTORIAL_PARA_CONTEXTO):
    resp = (
        cliente.table("candles")
        .select("timestamp, open, high, low, close, volume, variables")
        .eq("symbol", SYMBOL)
        .eq("timeframe", TIMEFRAME)
        .order("timestamp", desc=True)
        .limit(cuantas)
        .execute()
    )
    return list(reversed(resp.data))  # orden cronológico


def parsear_timestamp(valor):
    # Soporta que Supabase devuelva con o sin offset de zona horaria; se
    # trabaja todo como "naive" (igual que subir_velas.py), asumiendo UTC.
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


# Tu MT4/TradingView trabajan en UTC+3. Twelve Data, si no se le pide otra
# cosa, entrega forex en horario de Sídney (Australia) por defecto — por eso
# se pide explícitamente "UTC" y luego se suman las 3 horas de tu bróker.
HORAS_BROKER_RESPECTO_A_UTC = 3


def traer_velas_twelve_data(api_key, outputsize=500):
    respuesta = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": TWELVE_DATA_SYMBOL,
            "interval": TWELVE_DATA_INTERVAL,
            "outputsize": outputsize,
            "timezone": "UTC",
            "apikey": api_key,
        },
        timeout=30,
    )
    datos = respuesta.json()

    if datos.get("status") == "error" or "values" not in datos:
        raise RuntimeError(f"Error de Twelve Data: {datos}")

    velas = []
    for v in reversed(datos["values"]):  # Twelve Data da lo más reciente primero
        ts_utc = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
        ts_broker = ts_utc + timedelta(hours=HORAS_BROKER_RESPECTO_A_UTC)
        velas.append({
            "timestamp": ts_broker,
            "open": float(v["open"]),
            "high": float(v["high"]),
            "low": float(v["low"]),
            "close": float(v["close"]),
            "volume": float(v["volume"]) if v.get("volume") else 0.0,
        })
    return velas


def calcular_rsi(closes, periodo=14):
    if len(closes) < periodo + 1:
        return 50.0

    ganancias = perdidas = 0.0
    for i in range(1, periodo + 1):
        diff = closes[i] - closes[i - 1]
        ganancias += diff if diff >= 0 else 0
        perdidas += -diff if diff < 0 else 0
    avg_gain, avg_loss = ganancias / periodo, perdidas / periodo

    for i in range(periodo + 1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gain = diff if diff > 0 else 0
        loss = -diff if diff < 0 else 0
        avg_gain = (avg_gain * (periodo - 1) + gain) / periodo
        avg_loss = (avg_loss * (periodo - 1) + loss) / periodo

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def ema_serie(valores, periodo):
    k = 2 / (periodo + 1)
    serie = [None] * len(valores)
    ema = None
    for i in range(len(valores)):
        if i < periodo - 1:
            continue
        ema = sum(valores[i - periodo + 1: i + 1]) / periodo if ema is None else valores[i] * k + ema * (1 - k)
        serie[i] = ema
    return serie


def calcular_macd(closes, rapida=12, lenta=26, señal=9):
    ema_rapida = ema_serie(closes, rapida)
    ema_lenta = ema_serie(closes, lenta)
    macd_linea = [a - b for a, b in zip(ema_rapida, ema_lenta) if a is not None and b is not None]
    if not macd_linea:
        return 0.0, 0.0
    señal_serie = ema_serie(macd_linea, señal)
    return macd_linea[-1], señal_serie[-1]


def calcular_bollinger(closes, periodo=20, desviaciones=2):
    ultimos = closes[-periodo:]
    media = sum(ultimos) / len(ultimos)
    varianza = sum((c - media) ** 2 for c in ultimos) / len(ultimos)
    desviacion = varianza ** 0.5
    return media + desviaciones * desviacion, media, media - desviaciones * desviacion


def calcular_variables_derivadas(open_, high, low, close, bb_up, bb_mid, bb_dw, smma21, smma50, smma200):
    C, D, E, F = open_, high, low, close
    L, M, N = bb_up, bb_mid, bb_dw
    O, P, Q = smma21, smma50, smma200

    body_size = (F - C) * 10000
    upper_shadow = (D - F if body_size > 0 else C - D) * 10000
    lower_shadow = (C - E if body_size > 0 else F - E) * 10000
    full_size = abs(body_size) + abs(upper_shadow) + abs(lower_shadow)

    r = lambda n: round(n, 4)

    return {
        "BODY_SIZE": r(body_size), "UPPER_SHADOW": r(upper_shadow),
        "LOWER_SHADOW": r(lower_shadow), "FULL_SIZE": r(full_size),
        "OPEN-200": r((C - Q) * 10000), "OPEN-50": r((C - P) * 10000), "OPEN-21": r((C - O) * 10000),
        "OPEN-BBUP": r((C - L) * 10000), "OPEN-BBMID": r((C - M) * 10000), "OPEN-BBDW": r((C - N) * 10000),
        "CLOSE-200": r((F - Q) * 10000), "CLOSE-50": r((F - P) * 10000), "CLOSE-21": r((F - O) * 10000),
        "CLOSE-BBUP": r((F - L) * 10000), "CLOSE-BBMID": r((F - M) * 10000), "CLOSE-BBDW": r((F - N) * 10000),
        "HIGH-200": r((D - Q) * 10000), "HIGH-50": r((D - P) * 10000), "HIGH-21": r((D - O) * 10000),
        "HIGH-BBUP": r((D - L) * 10000), "HIGH-BBMID": r((D - M) * 10000), "HIGH-BBDW": r((D - N) * 10000),
        "LOW-200": r((E - Q) * 10000), "LOW-50": r((E - P) * 10000), "LOW-21": r((E - O) * 10000),
        "LOW-BBUP": r((E - L) * 10000), "LOW-BBMID": r((E - M) * 10000), "LOW-BBDW": r((E - N) * 10000),
    }


def procesar_y_subir(supabase_url, supabase_key, historial, nuevas):
    if not nuevas:
        print("No hay velas nuevas que traer. Ya estás al día.")
        return

    closes = [h["close"] for h in historial]

    ultima = historial[-1] if historial else None
    atr_prev = ultima["variables"]["ATR"] if ultima else None
    ema21_prev = ultima["variables"]["EMA_21"] if ultima else None
    smma21_prev = ultima["variables"]["SMMA_21"] if ultima else None
    smma50_prev = ultima["variables"]["SMMA_50"] if ultima else None
    smma200_prev = ultima["variables"]["SMMA_200"] if ultima else None
    close_prev = ultima["close"] if ultima else None

    filas = []

    for vela in nuevas:
        closes.append(vela["close"])

        tr = max(
            vela["high"] - vela["low"],
            abs(vela["high"] - close_prev) if close_prev is not None else 0,
            abs(vela["low"] - close_prev) if close_prev is not None else 0,
        )
        atr = tr if atr_prev is None else (atr_prev * 13 + tr) / 14

        k21 = 2 / 22
        ema21 = vela["close"] if ema21_prev is None else vela["close"] * k21 + ema21_prev * (1 - k21)
        smma21 = vela["close"] if smma21_prev is None else (smma21_prev * 20 + vela["close"]) / 21
        smma50 = vela["close"] if smma50_prev is None else (smma50_prev * 49 + vela["close"]) / 50
        smma200 = vela["close"] if smma200_prev is None else (smma200_prev * 199 + vela["close"]) / 200

        rsi = calcular_rsi(closes[-150:])
        macd_main, macd_signal = calcular_macd(closes[-150:])
        bb_up, bb_mid, bb_dw = calcular_bollinger(closes)

        derivadas = calcular_variables_derivadas(
            vela["open"], vela["high"], vela["low"], vela["close"],
            bb_up, bb_mid, bb_dw, smma21, smma50, smma200,
        )

        variables = {
            "RSI": round(rsi, 2), "ATR": round(atr, 5),
            "EMA_21": round(ema21, 5), "SMMA_21": round(smma21, 5),
            "SMMA_50": round(smma50, 5), "SMMA_200": round(smma200, 5),
            "MACD_MAIN": round(macd_main, 5), "MACD_SIGNAL": round(macd_signal, 5),
            "BB_UP": round(bb_up, 5), "BB_MID": round(bb_mid, 5), "BB_DW": round(bb_dw, 5),
            **derivadas,
        }

        filas.append({
            "symbol": SYMBOL,
            "timeframe": TIMEFRAME,
            "timestamp": vela["timestamp"].isoformat(),
            "open": vela["open"], "high": vela["high"], "low": vela["low"],
            "close": vela["close"], "volume": vela["volume"],
            "variables": variables,
        })

        atr_prev, ema21_prev = atr, ema21
        smma21_prev, smma50_prev, smma200_prev = smma21, smma50, smma200
        close_prev = vela["close"]

    respuesta = requests.post(
        f"{supabase_url}/rest/v1/candles?on_conflict=symbol,timeframe,timestamp",
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
        json=filas,
        timeout=60,
    )
    if respuesta.status_code >= 300:
        raise RuntimeError(f"Error subiendo velas: {respuesta.status_code} — {respuesta.text}")

    print(f"Listo. {len(filas)} vela(s) nueva(s) subida(s) "
          f"({filas[0]['timestamp']} -> {filas[-1]['timestamp']}).")


def main():
    config = cargar_config()
    cliente = create_client(config["supabase_url"], config["supabase_key"])

    print("Trayendo historial reciente de Supabase...")
    historial = traer_ultimas_velas_supabase(cliente)
    if historial:
        for h in historial:
            h["timestamp"] = parsear_timestamp(h["timestamp"])

    print("Trayendo velas de Twelve Data...")
    velas_td = traer_velas_twelve_data(config["twelve_data_key"])

    if historial:
        ultimo_ts = historial[-1]["timestamp"]
        nuevas = [v for v in velas_td if v["timestamp"] > ultimo_ts]
    else:
        nuevas = velas_td  # no hay nada guardado todavía: sube todo lo que trajo Twelve Data

    procesar_y_subir(config["supabase_url"], config["supabase_key"], historial, nuevas)


if __name__ == "__main__":
    main()
