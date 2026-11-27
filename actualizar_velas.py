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
  * La hora del bróker ya no es fija: se calcula con horario_broker.py
    (UTC+3 en verano de EE. UU., UTC+2 en invierno).
  * Twelve Data entrega velas también el sábado y el domingo, cuando el mercado
    está cerrado y MT4 no las tiene. Se descartan (sábado/domingo en hora del bróker).
  * El modo reparación pide el historial por tramos (Twelve Data da máx. 5000 velas
    por consulta), así que puede reparar rangos largos.

Uso normal (lo hace el workflow cada 15 min):
    python actualizar_velas.py

Reparar desde una fecha (hora del bróker):
    python actualizar_velas.py --reparar-desde "2026-09-26 00:00"
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
from horario_broker import utc_a_broker

SYMBOL = "AUDUSD"
TIMEFRAME = "M15"
TWELVE_DATA_SYMBOL = "AUD/USD"
TWELVE_DATA_INTERVAL = "15min"
MINUTOS_POR_VELA = 15

HISTORIAL_PARA_CONTEXTO = 250  # velas previas que se traen de Supabase, para
                                # dar contexto a RSI/MACD (no para reiniciar
                                # SMMA/EMA/ATR: esos continúan del último valor real)

# Twelve Data, si no se le pide otra cosa, entrega forex en horario de Sídney
# (Australia) por defecto — por eso se pide explícitamente "UTC" y luego se
# convierte a la hora de tu bróker con utc_a_broker() (UTC+2 / UTC+3).

INTENTOS_ESPERANDO_VELA = 3
SEGUNDOS_ENTRE_INTENTOS = 20

MAX_VELAS_POR_CONSULTA = 5000  # máximo que entrega Twelve Data por consulta
MAX_TRAMOS_REPARACION = 6      # tope de consultas en modo reparación


def ahora_en_broker():
    """Hora actual en el horario del bróker (naive, igual que los timestamps guardados)."""
    return utc_a_broker(datetime.now(timezone.utc).replace(tzinfo=None))


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


def quitar_horas_repetidas(velas):
    """Si dos filas quedan con la misma hora (repetidas por Twelve Data o por el
    cambio de horario), se conserva solo la última de cada una. Supabase rechaza
    un envío donde la misma vela aparece dos veces."""
    unicas = {}
    for v in velas:
        unicas[v["timestamp"]] = v  # conserva la posición de la primera, con los datos de la última

    repetidas = len(velas) - len(unicas)
    if repetidas:
        contador = {}
        for v in velas:
            contador[v["timestamp"]] = contador.get(v["timestamp"], 0) + 1
        ejemplos = [f"{ts:%Y-%m-%d %H:%M}" for ts, n in contador.items() if n > 1][:5]
        print(f"AVISO: {repetidas} vela(s) con hora repetida; se conserva la última de cada una. "
              f"Ejemplos: {', '.join(ejemplos)}")

    return list(unicas.values())


def pedir_valores_twelve_data(api_key, outputsize, end_date=None):
    """Una consulta a Twelve Data. Devuelve las filas tal cual (lo más reciente primero)."""
    params = {
        "symbol": TWELVE_DATA_SYMBOL,
        "interval": TWELVE_DATA_INTERVAL,
        "outputsize": outputsize,
        "timezone": "UTC",
        "apikey": api_key,
    }
    if end_date:
        params["end_date"] = end_date

    respuesta = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=30)
    datos = respuesta.json()

    if datos.get("status") == "error" or "values" not in datos:
        raise RuntimeError(f"Error de Twelve Data: {datos}")
    return datos["values"]


def traer_valores_por_tramos(api_key, desde_broker):
    """Modo reparación: Twelve Data entrega como máximo 5000 velas por consulta,
    así que se pide por tramos hacia atrás hasta cubrir la fecha pedida."""
    por_hora = {}
    end_date = None
    anterior = None

    for tramo in range(1, MAX_TRAMOS_REPARACION + 1):
        try:
            valores = pedir_valores_twelve_data(api_key, MAX_VELAS_POR_CONSULTA, end_date)
        except RuntimeError as error:
            if tramo == 1:
                raise
            print(f"AVISO: Twelve Data no entregó más historial: {error}")
            break

        for v in valores:
            por_hora[v["datetime"]] = v
        mas_antigua = min(por_hora)  # "AAAA-MM-DD HH:MM:SS" se ordena bien como texto
        print(f"Tramo {tramo}: {len(valores)} velas; la más antigua es {mas_antigua} UTC.")

        ya_cubierto = utc_a_broker(datetime.strptime(mas_antigua, "%Y-%m-%d %H:%M:%S")) <= desde_broker
        if len(valores) < MAX_VELAS_POR_CONSULTA or ya_cubierto or mas_antigua == anterior:
            break
        anterior = mas_antigua
        end_date = mas_antigua

    return sorted(por_hora.values(), key=lambda v: v["datetime"], reverse=True)


def traer_velas_twelve_data(api_key, outputsize, desde_broker=None):
    if desde_broker is None:
        valores = pedir_valores_twelve_data(api_key, outputsize)
    else:
        valores = traer_valores_por_tramos(api_key, desde_broker)

    velas = []
    omitidas = 0
    for v in reversed(valores):  # Twelve Data da lo más reciente primero
        ts_utc = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
        ts_broker = utc_a_broker(ts_utc)

        # Sábado y domingo (hora del bróker): el mercado está cerrado. En MT4 no
        # existen esas velas, así que no se guardan (distorsionarían los indicadores).
        if ts_broker.weekday() >= 5:
            omitidas += 1
            continue

        velas.append({
            "timestamp": ts_broker,
            "open": float(v["open"]),
            "high": float(v["high"]),
            "low": float(v["low"]),
            "close": float(v["close"]),
            "volume": float(v["volume"]) if v.get("volume") else 0.0,
        })

    if omitidas:
        print(f"Se omitieron {omitidas} vela(s) de sábado/domingo (mercado cerrado).")
    return quitar_horas_repetidas(velas)


def inicio_ultima_vela_cerrada(ahora):
    inicio_actual = ahora.replace(
        minute=(ahora.minute // MINUTOS_POR_VELA) * MINUTOS_POR_VELA, second=0, microsecond=0
    )
    return inicio_actual - timedelta(minutes=MINUTOS_POR_VELA)


def solo_velas_cerradas(velas, ahora):
    """Descarta la vela en formación: una vela está cerrada cuando ya pasó su hora de cierre."""
    return [v for v in velas if v["timestamp"] + timedelta(minutes=MINUTOS_POR_VELA) <= ahora]


def traer_velas_cerradas(api_key, outputsize, esperar_vela_nueva=True, desde_broker=None):
    """Trae velas de Twelve Data y devuelve solo las cerradas. Si la última vela
    cerrada esperada todavía no aparece, reintenta unas veces."""
    velas = []
    for intento in range(1, INTENTOS_ESPERANDO_VELA + 1):
        ahora = ahora_en_broker()
        velas = solo_velas_cerradas(traer_velas_twelve_data(api_key, outputsize, desde_broker), ahora)
        esperada = inicio_ultima_vela_cerrada(ahora)

        if velas and velas[-1]["timestamp"] >= esperada:
            return velas

        # Si es fin de semana, o la vela esperada cae en fin de semana, el mercado
        # está cerrado: no tiene caso esperar.
        fin_de_semana = ahora.weekday() >= 5 or esperada.weekday() >= 5
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
    # Acepta con o sin segundos, con espacio o con "T", y descarta la zona horaria si viene (+00, Z...)
    limpio = texto.strip().replace("T", " ")
    for corte in ("+", "Z"):
        limpio = limpio.split(corte)[0]
    limpio = limpio.strip()
    for formato in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(limpio, formato)
        except ValueError:
            continue
    raise SystemExit(f"Fecha inválida para --reparar-desde: '{texto}'. Usa 'YYYY-MM-DD HH:MM'.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--reparar-desde",
        default=os.environ.get("REPARAR_DESDE", ""),
        help="Reescribe todas las velas desde esta fecha (hora del bróker), ej. '2026-09-26 00:00'",
    )
    args = parser.parse_args()
    reparar_desde = parsear_fecha_reparacion(args.reparar_desde)

    api_key = os.environ["TWELVE_DATA_API_KEY"]

    if reparar_desde:
        outputsize = MAX_VELAS_POR_CONSULTA
        print(f"MODO REPARACIÓN desde {reparar_desde:%Y-%m-%d %H:%M} (por tramos de hasta {MAX_VELAS_POR_CONSULTA} velas).")
    else:
        outputsize = 500

    print("Trayendo velas cerradas de Twelve Data...")
    velas_td = traer_velas_cerradas(
        api_key, outputsize, esperar_vela_nueva=not reparar_desde, desde_broker=reparar_desde
    )
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
