"""
Trae las velas CERRADAS de AUDUSD M15 desde Twelve Data y las sube a Supabase,
calculando los indicadores en continuidad con el historial que ya tienes
(ATR, EMA_21 y SMMA_21/50/200 no se reinician: siguen la misma fórmula
recursiva a partir del último valor guardado).

Cambios importantes respecto a la versión anterior:
  * Twelve Data devuelve como última fila la vela que todavía se está
    formando (con OHLC parcial). Ahora esa vela se descarta: solo se suben
    velas ya cerradas.
  * Si la vela recién cerrada todavía no aparece en Twelve Data, se reintenta
    unas veces (cada 20 s) antes de rendirse hasta la siguiente corrida.
  * Si la última vela guardada tiene datos distintos a los que Twelve Data
    tiene ahora (por ejemplo, se guardó parcial), se recalcula sola.
  * Modo reparación: reescribe todas las velas desde una fecha. Sirve para
    arreglar las velas que quedaron guardadas parciales antes de este cambio.

Uso normal (lo hace el workflow cada 15 min):
    python actualizar_velas.py

Reparar desde una fecha (hora del bróker, UTC+3):
    python actualizar_velas.py --reparar-desde "2026-09-20 00:00"
    (también se puede pasar con la variable de entorno REPARAR_DESDE)

Requiere:
    pip install requests python-dotenv

Variables de entorno (archivo .env):
    SUPABASE_URL=...
    SUPABASE_SERVICE_ROLE_KEY=...
    TWELVE_DATA_API_KEY=...
"""

import argparse
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

import supabase_rest as db

SYMBOL = "AUDUSD"
TIMEFRAME = "M15"
TWELVE_DATA_SYMBOL = "AUD/USD"
TWELVE_DATA_INTERVAL = "15min"
MINUTOS_POR_VELA = 15

HISTORIAL_PARA_CONTEXTO = 250  # velas previas que se traen de Supabase, para
                                # dar contexto a RSI/MACD (no para reiniciar
                                # SMMA/EMA/ATR: esos continúan del último valor real)

# Tu MT4/TradingView trabajan en UTC+3. Twelve Data, si no se le pide otra
# cosa, entrega forex en horario de Sídney (Australia) por defecto — por eso
# se pide explícitamente "UTC" y luego se suman las 3 horas de tu bróker.
HORAS_BROKER_RESPECTO_A_UTC = 3

INTENTOS_ESPERANDO_VELA = 3
SEGUNDOS_ENTRE_INTENTOS = 20


def ahora_en_broker():
    """Hora actual en el horario del bróker (naive, igual que los timestamps guardados)."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=HORAS_BROKER_RESPECTO_A_UTC)


def parsear_timestamp(valor):
    # Soporta que Supabase devuelva con o sin offset de zona horaria; se
    # trabaja todo como "naive" (igual que subir_velas.py), asumiendo UTC.
    dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


def traer_ultimas_velas_supabase(cuantas=HISTORIAL_PARA_CONTEXTO, antes_de=None):
    params = {
        "select": "timestamp,open,high,low,close,volume,variables",
        "symbol": f"eq.{SYMBOL}",
        "timeframe": f"eq.{TIMEFRAME}",
        "order": "timestamp.desc",
        "limit": cuantas,
    }
    if antes_de is not None:
        params["timestamp"] = f"lt.{antes_de.isoformat()}"
    filas = db.seleccionar("candles", params)
    return list(reversed(filas))  # orden cronológico


def traer_velas_twelve_data(api_key, outputsize):
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


def inicio_ultima_vela_cerrada(ahora):
    inicio_actual = ahora.replace(
        minute=(ahora.minute // MINUTOS_POR_VELA) * MINUTOS_POR_VELA, second=0, microsecond=0
    )
    return inicio_actual - timedelta(minutes=MINUTOS_POR_VELA)


def solo_velas_cerradas(velas, ahora):
    """Descarta la vela en formación: una vela está cerrada cuando ya pasó su hora de cierre."""
    return [v for v in velas if v["timestamp"] + timedelta(minutes=MINUTOS_POR_VELA) <= ahora]


def traer_velas_cerradas(api_key, outputsize, esperar_vela_nueva=True):
    """Trae velas de Twelve Data y devuelve solo las cerradas. Si la última vela
    cerrada esperada todavía no aparece, reintenta unas veces."""
    velas = []
    for intento in range(1, INTENTOS_ESPERANDO_VELA + 1):
        ahora = ahora_en_broker()
        velas = solo_velas_cerradas(traer_velas_twelve_data(api_key, outputsize), ahora)
        esperada = inicio_ultima_vela_cerrada(ahora)

        if velas and velas[-1]["timestamp"] >= esperada:
            return velas

        fin_de_semana = ahora.weekday() >= 5  # el mercado está cerrado: no tiene caso esperar
        if not esperar_vela_nueva or fin_de_semana or intento == INTENTOS_ESPERANDO_VELA:
            break

        print(f"Aún no aparece la vela cerrada de las {esperada:%H:%M}; reintento en "
              f"{SEGUNDOS_ENTRE_INTENTOS} s ({intento}/{INTENTOS_ESPERANDO_VELA})...")
        time.sleep(SEGUNDOS_ENTRE_INTENTOS)

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
    if señal_serie[-1] is None:  # aún no hay suficientes velas para la línea de señal
        return macd_linea[-1], 0.0
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


def construir_filas(historial, nuevas):
    """Calcula los indicadores de cada vela nueva, en continuidad con el historial."""
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

    return filas


def ohlc_distinto(guardada, de_twelve_data, tolerancia=1e-7):
    return any(
        abs(guardada[campo] - de_twelve_data[campo]) > tolerancia
        for campo in ("open", "high", "low", "close")
    )


def parsear_fecha_reparacion(texto):
    if not texto:
        return None
    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto.strip(), formato)
        except ValueError:
            continue
    raise SystemExit(f"Fecha inválida para --reparar-desde: '{texto}'. Usa 'YYYY-MM-DD HH:MM'.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reparar-desde",
        default=os.environ.get("REPARAR_DESDE", ""),
        help="Reescribe todas las velas desde esta fecha (hora del bróker), ej. '2026-09-20 00:00'",
    )
    args = parser.parse_args()
    reparar_desde = parsear_fecha_reparacion(args.reparar_desde)

    api_key = os.environ["TWELVE_DATA_API_KEY"]

    if reparar_desde:
        horas_atras = (ahora_en_broker() - reparar_desde).total_seconds() / 3600
        outputsize = min(5000, int(horas_atras * 4) + 60)
        print(f"MODO REPARACIÓN desde {reparar_desde:%Y-%m-%d %H:%M} (se piden {outputsize} velas).")
    else:
        outputsize = 500

    print("Trayendo velas cerradas de Twelve Data...")
    velas_td = traer_velas_cerradas(api_key, outputsize, esperar_vela_nueva=not reparar_desde)
    if not velas_td:
        print("Twelve Data no devolvió velas cerradas.")
        return

    print("Trayendo historial reciente de Supabase...")
    historial = traer_ultimas_velas_supabase(antes_de=reparar_desde)
    for h in historial:
        h["timestamp"] = parsear_timestamp(h["timestamp"])

    if reparar_desde:
        nuevas = [v for v in velas_td if v["timestamp"] >= reparar_desde]
        if velas_td[0]["timestamp"] > reparar_desde:
            print(f"AVISO: Twelve Data solo llega hasta {velas_td[0]['timestamp']:%Y-%m-%d %H:%M}; "
                  "las velas anteriores a esa fecha no se repararon.")
    elif historial:
        # Si la última vela guardada difiere de la versión actual de Twelve Data
        # (p. ej. se guardó cuando aún estaba abierta), se recalcula.
        td_por_ts = {v["timestamp"]: v for v in velas_td}
        ultima = historial[-1]
        misma = td_por_ts.get(ultima["timestamp"])
        if misma and len(historial) > 1 and ohlc_distinto(ultima, misma):
            print(f"La vela {ultima['timestamp']:%Y-%m-%d %H:%M} estaba guardada con datos distintos; se recalcula.")
            historial.pop()

        ultimo_ts = historial[-1]["timestamp"]
        nuevas = [v for v in velas_td if v["timestamp"] > ultimo_ts]

        if velas_td[0]["timestamp"] > ultimo_ts + timedelta(minutes=MINUTOS_POR_VELA):
            print("AVISO: puede haber un hueco entre la última vela guardada y las que trae Twelve Data. "
                  "Si es así, corre el workflow a mano con 'reparar_desde' = fecha de la última vela buena.")
    else:
        nuevas = velas_td  # no hay nada guardado todavía: sube todo lo que trajo Twelve Data

    if not nuevas:
        print("No hay velas nuevas que subir. Ya estás al día.")
        return

    filas = construir_filas(historial, nuevas)
    db.insertar("candles", filas, on_conflict="symbol,timeframe,timestamp")

    print(f"Listo. {len(filas)} vela(s) subida(s) ({filas[0]['timestamp']} -> {filas[-1]['timestamp']}).")


if __name__ == "__main__":
    main()
