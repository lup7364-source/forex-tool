const SUPABASE_URL = "https://zbndbecsqhzfmblgpgiw.supabase.co";
const SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpibmRiZWNzcWh6Zm1ibGdwZ2l3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3OTAxNDgwNDksImV4cCI6MjEwNTcyNDA0OX0.w8i7iYL8Hylmd178ad3jBdNhTuit6-XmNf8CcHZVnOI";

const supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

async function manejarLogin() {
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const mensajeLogin = document.getElementById("mensajeLogin");

  const { error } = await supabaseClient.auth.signInWithPassword({ email, password });

  if (error) {
    mensajeLogin.textContent = "Error: " + error.message;
    return;
  }

  mensajeLogin.textContent = "";
  mostrarApp();
}

async function manejarLogout() {
  await supabaseClient.auth.signOut();
  mostrarLogin();
}

function mostrarApp() {
  document.getElementById("status").textContent = "Sesión iniciada.";
  document.getElementById("login").style.display = "none";
  document.getElementById("buscador").style.display = "block";
  document.getElementById("btnLogout").style.display = "inline-block";
}

function mostrarLogin() {
  document.getElementById("status").textContent = "";
  document.getElementById("login").style.display = "block";
  document.getElementById("buscador").style.display = "none";
  document.getElementById("btnLogout").style.display = "none";
}

async function revisarSesion() {
  const { data } = await supabaseClient.auth.getSession();
  if (data.session) {
    mostrarApp();
  } else {
    mostrarLogin();
  }
}

document.getElementById("btnLogin").addEventListener("click", manejarLogin);
document.getElementById("btnLogout").addEventListener("click", manejarLogout);

async function buscarVela() {
  const symbol = document.getElementById("symbol").value.trim().toUpperCase();
  const timeframe = document.getElementById("timeframe").value.trim().toUpperCase();
  const fecha = document.getElementById("fecha").value;   // YYYY-MM-DD
  const hora = document.getElementById("hora").value;     // 00-23
  const minuto = document.getElementById("minuto").value; // 00/15/30/45

  if (!symbol || !timeframe || !fecha || hora === "") {
    alert("Completa símbolo, timeframe, fecha y hora antes de buscar.");
    return;
  }

  const timestampISO = `${fecha}T${hora}:${minuto}:00Z`; // se usa tal cual, sin reinterpretar como hora local

  const { data, error } = await supabaseClient
    .from("candles")
    .select("id")
    .eq("symbol", symbol)
    .eq("timeframe", timeframe)
    .eq("timestamp", timestampISO)
    .maybeSingle();

  if (error) {
    alert("Error buscando la vela: " + error.message);
    console.error(error);
    return;
  }

  if (!data) {
    alert("No se encontró ninguna vela con esos datos exactos.");
    return;
  }

  // Abre la misma vista completa que los puntos marcados (gráfico, indicadores,
  // variables), pero con los controles para marcar Buy/Sell.
  await verDetallePunto(data.id, null, true);
}

async function marcarPunto(direccion, tipo) {
  if (!velaDetalleActual) return;

  const nota = document.getElementById("notaDetalle").value.trim();
  const mensajeMarcado = document.getElementById("mensajeMarcadoDetalle");

  const { error } = await supabaseClient.from("points").insert({
    candle_id: velaDetalleActual.id,
    tipo: tipo,
    direccion: direccion,
    nota: nota || null,
  });

  if (error) {
    mensajeMarcado.textContent = "Error al marcar: " + error.message;
    console.error(error);
    return;
  }

  mensajeMarcado.textContent = `Vela marcada como "${direccion.toUpperCase()} ${tipo === "bueno" ? "O" : "X"}" correctamente.`;
}

const chartsPorContenedor = {}; // { containerId: { chart, serie } }

function inicializarChart(containerId) {
  if (chartsPorContenedor[containerId]) return chartsPorContenedor[containerId];

  const chart = LightweightCharts.createChart(document.getElementById(containerId), {
    height: containerId === "chartDetalle" ? 380 : 260,
    layout: {
      background: { color: "#FFFFFF" },
      textColor: "#222222",
    },
    grid: {
      vertLines: { color: "#EAEAEA" },
      horzLines: { color: "#EAEAEA" },
    },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: "#CCCCCC" },
  });

  const serie = chart.addCandlestickSeries({
    upColor: "#FFFFFF",
    downColor: "#000000",
    borderUpColor: "#000000",
    borderDownColor: "#000000",
    wickUpColor: "#000000",
    wickDownColor: "#000000",
    priceFormat: { type: "price", precision: 5, minMove: 0.00001 },
  });

  chartsPorContenedor[containerId] = { chart, serie };
  return chartsPorContenedor[containerId];
}

async function traerRangoVelas(vela) {
  const RANGO_VELAS = 40;
  const MS_POR_VELA = 15 * 60 * 1000;

  const centro = new Date(vela.timestamp);
  const desde = new Date(centro.getTime() - RANGO_VELAS * MS_POR_VELA).toISOString();
  const hasta = new Date(centro.getTime() + RANGO_VELAS * MS_POR_VELA).toISOString();

  const { data, error } = await supabaseClient
    .from("candles")
    .select("timestamp, open, high, low, close, volume, variables")
    .eq("symbol", vela.symbol)
    .eq("timeframe", vela.timeframe)
    .gte("timestamp", desde)
    .lte("timestamp", hasta)
    .order("timestamp", { ascending: true });

  if (error) {
    console.error("Error cargando rango de velas:", error);
    return [];
  }
  return data;
}

async function dibujarGraficoAlrededorDe(vela, containerId = "chart") {
  const { chart, serie } = inicializarChart(containerId);
  const data = await traerRangoVelas(vela);

  const datos = data.map((c) => {
    const esLaVelaBuscada = c.timestamp === vela.timestamp;
    const punto = {
      time: Math.floor(new Date(c.timestamp).getTime() / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    };

    if (esLaVelaBuscada) {
      punto.color = "#F5C518";
      punto.borderColor = "#F5C518";
      punto.wickColor = "#F5C518";
    }

    return punto;
  });

  serie.setData(datos);
  chart.timeScale().fitContent();
  return data;
}
function llenarSelectHoras() {
  const select = document.getElementById("hora");
  for (let h = 0; h < 24; h++) {
    const valor = String(h).padStart(2, "0");
    const opcion = document.createElement("option");
    opcion.value = valor;
    opcion.textContent = valor;
    select.appendChild(opcion);
  }
}
llenarSelectHoras();

async function buscarPuntoSimilar() {
  if (!velaDetalleActual) return;

  document.getElementById("cargandoSimilarDetalle").style.display = "block";
  document.getElementById("resultadoSimilarDetalle").style.display = "none";

  // 1. Traer todas las variables de todas las velas, para calcular el rango
  //    (min/max) real de cada variable y así normalizar las diferencias.
  const { data: todasLasVelas, error: errorVelas } = await supabaseClient
    .from("candles")
    .select("variables");

  if (errorVelas) {
    alert("Error trayendo velas para normalizar: " + errorVelas.message);
    return;
  }

  const minMax = {};
  for (const fila of todasLasVelas) {
    for (const [clave, valor] of Object.entries(fila.variables || {})) {
      if (valor === null || valor === undefined) continue;
      if (!minMax[clave]) minMax[clave] = [valor, valor];
      minMax[clave][0] = Math.min(minMax[clave][0], valor);
      minMax[clave][1] = Math.max(minMax[clave][1], valor);
    }
  }

  // 2. Traer todos los puntos marcados, con los datos de su vela
  const { data: puntos, error: errorPuntos } = await supabaseClient
    .from("points")
    .select("id, tipo, nota, candles(id, symbol, timeframe, timestamp, variables)");

  if (errorPuntos) {
    alert("Error trayendo puntos: " + errorPuntos.message);
    return;
  }

  if (!puntos || puntos.length === 0) {
    document.getElementById("cargandoSimilarDetalle").style.display = "none";
    alert("Todavía no hay puntos marcados para comparar.");
    return;
  }

  // 3. Calcular el % de diferencia de cada punto marcado contra la vela actual
  const actual = velaDetalleActual.variables || {};

  const candidatos = puntos
    .filter((p) => p.candles && p.candles.id !== velaDetalleActual.id) // no comparar contra sí misma
    .map((p) => {
      const otras = p.candles.variables || {};
      let sumaPct = 0;
      let contador = 0;
      const detalle = [];

      for (const clave of Object.keys(minMax)) {
        const v1 = actual[clave];
        const v2 = otras[clave];
        if (v1 === undefined || v2 === undefined) continue;

        const [min, max] = minMax[clave];
        const rango = max - min || 1; // evita división por cero
        const diffRaw = v2 - v1;
        const diffPct = (Math.abs(diffRaw) / rango) * 100;

        sumaPct += diffPct;
        contador++;

        detalle.push({ variable: clave, v1, v2, diffRaw, diffPct });
      }

      const promedio = contador > 0 ? sumaPct / contador : 100;
      return { punto: p, promedio, detalle };
    });

  candidatos.sort((a, b) => a.promedio - b.promedio);
  const mejor = candidatos[0];

  mostrarResultadoSimilar(mejor);
}

function mostrarResultadoSimilar(mejor) {
  document.getElementById("cargandoSimilarDetalle").style.display = "none";
  const resultadoSimilar = document.getElementById("resultadoSimilarDetalle");
  resultadoSimilar.style.display = "block";

  const c = mejor.punto.candles;
  document.getElementById("infoSimilarDetalle").textContent =
    `${c.symbol} ${c.timeframe} — ${c.timestamp.replace("T", " ").slice(0, 16)} | ` +
    `Tipo: ${mejor.punto.tipo.toUpperCase()}` +
    (mejor.punto.nota ? ` | Nota: ${mejor.punto.nota}` : "");

  document.getElementById("porcentajeTotalDetalle").textContent =
    mejor.promedio.toFixed(2) + "%" + (mejor.promedio === 0 ? " (coincidencia exacta)" : "");

  const cuerpo = document.querySelector("#tablaVariablesDetalle tbody");
  cuerpo.innerHTML = "";

  // ordena mostrando primero las variables con mayor diferencia
  const detalleOrdenado = [...mejor.detalle].sort((a, b) => b.diffPct - a.diffPct);

  for (const d of detalleOrdenado) {
    const fila = document.createElement("tr");
    if (d.diffRaw === 0) fila.classList.add("match-exact"); // resalta coincidencia exacta

    const direccion = d.diffRaw > 0 ? "arriba" : d.diffRaw < 0 ? "abajo" : "igual";

    fila.innerHTML = `
      <td>${d.variable}</td>
      <td>${d.v1}</td>
      <td>${d.v2}</td>
      <td>${d.diffRaw.toFixed(4)} (${direccion})</td>
      <td>${d.diffPct.toFixed(2)}%</td>
    `;
    cuerpo.appendChild(fila);
  }
}

async function verPuntosMarcados() {
  const contenedor = document.getElementById("puntosContenedor");
  const listaPuntos = document.getElementById("listaPuntos");

  const { data: puntos, error } = await supabaseClient
    .from("points")
    .select("id, tipo, direccion, nota, resultado, candles(id, symbol, timeframe, timestamp, close)")
    .order("id", { ascending: false });

  if (error) {
    alert("Error trayendo los puntos: " + error.message);
    return;
  }

  const conteo = { exitoso: 0, fallido: 0, pendiente: 0 };
  contenedor.innerHTML = "";

  for (const p of puntos) {
    const resultado = p.resultado || "pendiente";
    conteo[resultado] = (conteo[resultado] || 0) + 1;

    const c = p.candles;
    const fila = document.createElement("div");
    fila.className = "punto-item punto-" + resultado;
    fila.style.cursor = "pointer";
    fila.dataset.candleId = p.candles ? p.candles.id : "";
    fila.dataset.resultado = resultado;

    const fechaTexto = c ? c.timestamp.replace("T", " ").slice(0, 16) : "vela no encontrada";

    fila.innerHTML = `
      <span class="punto-badge">${p.direccion ? p.direccion.toUpperCase() : "?"} ${p.tipo === "bueno" ? "O" : p.tipo === "malo" ? "X" : "?"}</span>
      <span class="punto-fecha">${c ? c.symbol + " " + c.timeframe : ""} — ${fechaTexto}</span>
      <span class="punto-resultado">${resultado}</span>
    `;
    contenedor.appendChild(fila);
  }

  document.getElementById("totalExitosos").textContent = conteo.exitoso;
  document.getElementById("totalFallidos").textContent = conteo.fallido;
  document.getElementById("totalPendientes").textContent = conteo.pendiente;

  listaPuntos.style.display = "block";
}

let velaDetalleActual = null;
let rangoVelasDetalle = [];

async function verDetallePunto(candleId, resultado, modoMarcar = false) {
  if (!candleId) return;

  const { data, error } = await supabaseClient
    .from("candles")
    .select("*")
    .eq("id", candleId)
    .maybeSingle();

  if (error || !data) {
    alert("No se pudo cargar la vela: " + (error ? error.message : "no encontrada"));
    return;
  }

  velaDetalleActual = data;
  limpiarIndicadores();

  // Al abrir un nuevo punto, se limpia cualquier comparación/superposición
  // que hubiera quedado activa del punto anterior.
  quitarOverlayVelas();
  ultimaComparacion = null;
  document.getElementById("chkSuperponer").checked = false;
  document.getElementById("comparacionContainer").style.display = "none";
  document.getElementById("resultadoSimilaresGlobal").style.display = "none";

  document.getElementById("infoVelaDetalle").textContent =
    `${data.symbol} ${data.timeframe} — ${data.timestamp.replace("T", " ").slice(0, 16)} | ${resultado || "sin evaluar"}`;

  document.getElementById("paginaDetalle").classList.add("abierto");

  // Los controles de marcado solo aparecen cuando se llega desde "Buscar vela".
  document.getElementById("accionesDesdeBusqueda").style.display = modoMarcar ? "block" : "none";
  if (modoMarcar) {
    document.getElementById("notaDetalle").value = "";
    document.getElementById("mensajeMarcadoDetalle").textContent = "";
    document.getElementById("resultadoSimilarDetalle").style.display = "none";
    document.getElementById("cargandoSimilarDetalle").style.display = "none";
  }

  rangoVelasDetalle = await dibujarGraficoAlrededorDe(data, "chartDetalle");
  const variablesCompletas = {
    OPEN: data.open,
    HIGH: data.high,
    LOW: data.low,
    CLOSE: data.close,
    VOLUME: data.volume,
    ...(data.variables || {}),
  };
  mostrarVariablesVela(variablesCompletas, "variablesVelaDetalle");
}

function cerrarDetalle() {
  document.getElementById("paginaDetalle").classList.remove("abierto");
}

const ORDEN_VARIABLES = [
  // Precio y volumen
  "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME",
  // Indicadores base
  "RSI", "ATR", "EMA_21", "SMMA_21", "SMMA_50", "SMMA_200",
  // MACD
  "MACD_MAIN", "MACD_SIGNAL",
  // Bollinger (bandas + todas sus distancias)
  "BB_UP", "BB_MID", "BB_DW",
  "OPEN-BBUP", "OPEN-BBMID", "OPEN-BBDW",
  "CLOSE-BBUP", "CLOSE-BBMID", "CLOSE-BBDW",
  "HIGH-BBUP", "HIGH-BBMID", "HIGH-BBDW",
  "LOW-BBUP", "LOW-BBMID", "LOW-BBDW",
  // Distancia a medias móviles
  "OPEN-200", "OPEN-50", "OPEN-21",
  "CLOSE-200", "CLOSE-50", "CLOSE-21",
  "HIGH-200", "HIGH-50", "HIGH-21",
  "LOW-200", "LOW-50", "LOW-21",
  // Cuerpo y sombras de la vela
  "BODY_SIZE", "UPPER_SHADOW", "LOWER_SHADOW", "FULL_SIZE",
];

const DOS_DECIMALES = new Set([
  "RSI",
  "OPEN-BBUP", "OPEN-BBMID", "OPEN-BBDW",
  "CLOSE-BBUP", "CLOSE-BBMID", "CLOSE-BBDW",
  "HIGH-BBUP", "HIGH-BBMID", "HIGH-BBDW",
  "LOW-BBUP", "LOW-BBMID", "LOW-BBDW",
  "OPEN-200", "OPEN-50", "OPEN-21",
  "CLOSE-200", "CLOSE-50", "CLOSE-21",
  "HIGH-200", "HIGH-50", "HIGH-21",
  "LOW-200", "LOW-50", "LOW-21",
  "BODY_SIZE", "UPPER_SHADOW", "LOWER_SHADOW", "FULL_SIZE",
]);

function formatearValor(clave, valor) {
  const numero = Number(valor);
  if (!Number.isFinite(numero)) return valor;
  if (clave === "VOLUME") return numero.toFixed(0);
  if (DOS_DECIMALES.has(clave)) return numero.toFixed(2);
  return numero.toFixed(5);
}

function mostrarVariablesVela(variables, containerId) {
  const contenedor = document.getElementById(containerId);
  contenedor.innerHTML = "";

  const claves = Object.keys(variables);
  const ordenadas = [
    ...ORDEN_VARIABLES.filter((clave) => claves.includes(clave)),
    ...claves.filter((clave) => !ORDEN_VARIABLES.includes(clave)),
  ];

  for (const clave of ordenadas) {
    const tarjeta = document.createElement("div");
    tarjeta.className = "variable-card";
    tarjeta.innerHTML = `
      <span class="v-nombre">${clave}</span>
      <span class="v-valor">${formatearValor(clave, variables[clave])}</span>
    `;
    contenedor.appendChild(tarjeta);
  }
}

document.getElementById("btnCerrarDetalle").addEventListener("click", cerrarDetalle);

// ---------- Indicadores toggleables sobre el gráfico de detalle ----------

const indicadoresActivos = {};

function datosSerieIndicador(clave, esVariable) {
  return rangoVelasDetalle.map((c) => ({
    time: Math.floor(new Date(c.timestamp).getTime() / 1000),
    value: esVariable ? c.variables[clave] : c[clave],
  }));
}

function tiempoVelaCentral() {
  return Math.floor(new Date(velaDetalleActual.timestamp).getTime() / 1000);
}

function marcadorAmarillo() {
  return [{ time: tiempoVelaCentral(), position: "inBar", color: "#F5C518", shape: "circle" }];
}

function agregarBB() {
  const { chart } = chartsPorContenedor["chartDetalle"];
  const up = chart.addLineSeries({ color: "#2DD4BF", lineWidth: 1, priceLineVisible: false });
  const mid = chart.addLineSeries({ color: "#8CA0A0", lineWidth: 1, priceLineVisible: false });
  const dw = chart.addLineSeries({ color: "#2DD4BF", lineWidth: 1, priceLineVisible: false });
  up.setData(datosSerieIndicador("BB_UP", true));
  mid.setData(datosSerieIndicador("BB_MID", true));
  dw.setData(datosSerieIndicador("BB_DW", true));
  up.setMarkers(marcadorAmarillo());
  indicadoresActivos.BB = [up, mid, dw];
}

function crearOReusarSubChart(containerId, tipoSerie, colorSerie, priceFormat) {
  if (chartsPorContenedor[containerId]) return chartsPorContenedor[containerId];

  const chart = LightweightCharts.createChart(document.getElementById(containerId), {
    height: 130,
    layout: { background: { color: "#FFFFFF" }, textColor: "#222222" },
    grid: { vertLines: { color: "#EAEAEA" }, horzLines: { color: "#EAEAEA" } },
    timeScale: { timeVisible: true, secondsVisible: false },
    rightPriceScale: { borderColor: "#CCCCCC" },
  });

  // priceFormat opcional: permite mostrar escalas con más decimales
  // (por ejemplo el ATR, que en forex suele ser algo como 0.00035).
  const opcionesSerie = { priceLineVisible: false };
  if (priceFormat) opcionesSerie.priceFormat = priceFormat;

  const serie =
    tipoSerie === "histogram"
      ? chart.addHistogramSeries({ color: colorSerie, ...opcionesSerie })
      : chart.addLineSeries({ color: colorSerie, lineWidth: 1, ...opcionesSerie });

  chartsPorContenedor[containerId] = { chart, serie };
  return chartsPorContenedor[containerId];
}

function agregarRSI() {
  document.getElementById("cardRSI").style.display = "block";
  const { chart, serie } = crearOReusarSubChart("chartRSI", "line", "#F59E0B");
  serie.setData(datosSerieIndicador("RSI", true));
  serie.setMarkers(marcadorAmarillo());
  chart.timeScale().fitContent();
}

function agregarATR() {
  document.getElementById("cardATR").style.display = "block";
  // Escala con 5 decimales (tipo 0.00035) para que el ATR se lea bien.
  const { chart, serie } = crearOReusarSubChart("chartATR", "line", "#A78BFA", {
    type: "price",
    precision: 5,
    minMove: 0.00001,
  });
  serie.setData(datosSerieIndicador("ATR", true));
  serie.setMarkers(marcadorAmarillo());
  chart.timeScale().fitContent();
}

function agregarVolume() {
  document.getElementById("cardVolume").style.display = "block";
  const { chart, serie } = crearOReusarSubChart("chartVolume", "histogram", "#8CA0A0");
  serie.setData(datosSerieIndicador("volume", false));
  serie.setMarkers(marcadorAmarillo());
  chart.timeScale().fitContent();
}

function ocultarSubChart(cardId) {
  document.getElementById(cardId).style.display = "none";
}

function agregarMediaMovil(nombre, clave, color) {
  const { chart } = chartsPorContenedor["chartDetalle"];
  const serie = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false });
  serie.setData(datosSerieIndicador(clave, true));
  serie.setMarkers(marcadorAmarillo());
  indicadoresActivos[nombre] = [serie];
}

function quitarIndicador(nombre) {
  const { chart } = chartsPorContenedor["chartDetalle"] || {};
  if (!chart || !indicadoresActivos[nombre]) return;
  for (const serie of indicadoresActivos[nombre]) chart.removeSeries(serie);
  delete indicadoresActivos[nombre];
}

function limpiarIndicadores() {
  for (const nombre of Object.keys(indicadoresActivos)) quitarIndicador(nombre);
  for (const nombre of Object.keys(indicadoresActivosSimilar)) quitarIndicadorSimilar(nombre);
  ["cardRSI", "cardATR", "cardVolume"].forEach(ocultarSubChart);
  ["chkBB", "chkRSI", "chkATR", "chkVolume", "chkEMA21", "chkSMMA21", "chkSMMA50", "chkSMMA200"].forEach((id) => {
    document.getElementById(id).checked = false;
  });
  [
    "chkBBSimilar", "chkEMA21Similar", "chkSMMA21Similar", "chkSMMA50Similar", "chkSMMA200Similar",
  ].forEach((id) => {
    document.getElementById(id).checked = false;
  });
}

document.getElementById("chkBB").addEventListener("change", (e) =>
  e.target.checked ? agregarBB() : quitarIndicador("BB")
);
document.getElementById("chkRSI").addEventListener("change", (e) => {
  if (e.target.checked) agregarRSI();
  actualizarCardRSI();
});
document.getElementById("chkATR").addEventListener("change", (e) => {
  if (e.target.checked) agregarATR();
  actualizarCardATR();
});
document.getElementById("chkVolume").addEventListener("change", (e) => {
  if (e.target.checked) agregarVolume();
  actualizarCardVolume();
});
document.getElementById("chkEMA21").addEventListener("change", (e) =>
  e.target.checked ? agregarMediaMovil("EMA21", "EMA_21", "#38BDF8") : quitarIndicador("EMA21")
);
document.getElementById("chkSMMA21").addEventListener("change", (e) =>
  e.target.checked ? agregarMediaMovil("SMMA21", "SMMA_21", "#F87171") : quitarIndicador("SMMA21")
);
document.getElementById("chkSMMA50").addEventListener("change", (e) =>
  e.target.checked ? agregarMediaMovil("SMMA50", "SMMA_50", "#FB923C") : quitarIndicador("SMMA50")
);
document.getElementById("chkSMMA200").addEventListener("change", (e) =>
  e.target.checked ? agregarMediaMovil("SMMA200", "SMMA_200", "#C084FC") : quitarIndicador("SMMA200")
);

// ---------- Los mismos indicadores, pero para el punto similar (en colores distintos) ----------

document.getElementById("chkBBSimilar").addEventListener("change", (e) => {
  if (e.target.checked) {
    if (!requiereComparacionActiva(e.target)) return;
    agregarBBSimilar();
  } else {
    quitarIndicadorSimilar("BB");
  }
});
document.getElementById("chkEMA21Similar").addEventListener("change", (e) => {
  if (e.target.checked) {
    if (!requiereComparacionActiva(e.target)) return;
    agregarMediaMovilSimilar("EMA21", "EMA_21", "#F472B6");
  } else {
    quitarIndicadorSimilar("EMA21");
  }
});
document.getElementById("chkSMMA21Similar").addEventListener("change", (e) => {
  if (e.target.checked) {
    if (!requiereComparacionActiva(e.target)) return;
    agregarMediaMovilSimilar("SMMA21", "SMMA_21", "#EF4444");
  } else {
    quitarIndicadorSimilar("SMMA21");
  }
});
document.getElementById("chkSMMA50Similar").addEventListener("change", (e) => {
  if (e.target.checked) {
    if (!requiereComparacionActiva(e.target)) return;
    agregarMediaMovilSimilar("SMMA50", "SMMA_50", "#F97316");
  } else {
    quitarIndicadorSimilar("SMMA50");
  }
});
document.getElementById("chkSMMA200Similar").addEventListener("change", (e) => {
  if (e.target.checked) {
    if (!requiereComparacionActiva(e.target)) return;
    agregarMediaMovilSimilar("SMMA200", "SMMA_200", "#A855F7");
  } else {
    quitarIndicadorSimilar("SMMA200");
  }
});

// ---------- Búsqueda de puntos similares en TODA la base de datos ----------

async function buscarSimilaresGlobal() {
  if (!velaDetalleActual) return;

  document.getElementById("cargandoSimilaresGlobal").style.display = "block";
  document.getElementById("resultadoSimilaresGlobal").style.display = "none";

  const { data: todasLasVelas, error: errorVelas } = await supabaseClient
    .from("candles")
    .select("variables")
    .eq("symbol", velaDetalleActual.symbol)
    .eq("timeframe", velaDetalleActual.timeframe);

  if (errorVelas) {
    alert("Error trayendo velas para normalizar: " + errorVelas.message);
    return;
  }

  const minMax = {};
  for (const fila of todasLasVelas) {
    for (const [clave, valor] of Object.entries(fila.variables || {})) {
      if (valor === null || valor === undefined) continue;
      if (!minMax[clave]) minMax[clave] = [valor, valor];
      minMax[clave][0] = Math.min(minMax[clave][0], valor);
      minMax[clave][1] = Math.max(minMax[clave][1], valor);
    }
  }

  const { data: candidatas, error: errorCand } = await supabaseClient
    .from("candles")
    .select("id, symbol, timeframe, timestamp, variables")
    .eq("symbol", velaDetalleActual.symbol)
    .eq("timeframe", velaDetalleActual.timeframe);

  if (errorCand) {
    alert("Error trayendo velas candidatas: " + errorCand.message);
    return;
  }

  // Traer el resultado (si existe) de los puntos ya marcados para estas velas
  const { data: puntosExistentes, error: errorPuntosExistentes } = await supabaseClient
    .from("points")
    .select("candle_id, resultado, candles!inner(symbol, timeframe)")
    .eq("candles.symbol", velaDetalleActual.symbol)
    .eq("candles.timeframe", velaDetalleActual.timeframe);

  if (errorPuntosExistentes) {
    alert("Error trayendo puntos existentes: " + errorPuntosExistentes.message);
    return;
  }

  const resultadoPorCandleId = new Map();
  for (const p of puntosExistentes) {
    resultadoPorCandleId.set(p.candle_id, p.resultado || "pendiente");
  }

  const actual = velaDetalleActual.variables || {};

  const resultados = candidatas
    .filter((c) => c.id !== velaDetalleActual.id)
    .map((c) => {
      let suma = 0;
      let contador = 0;
      for (const clave of Object.keys(minMax)) {
        const v1 = actual[clave];
        const v2 = c.variables[clave];
        if (v1 === undefined || v2 === undefined) continue;
        const [min, max] = minMax[clave];
        const rango = max - min || 1;
        suma += (Math.abs(v2 - v1) / rango) * 100;
        contador++;
      }
      const promedio = contador > 0 ? suma / contador : 100;
      return { candle: c, promedio, estado: resultadoPorCandleId.get(c.id) || "no evaluado" };
    });

  resultados.sort((a, b) => a.promedio - b.promedio);
  mostrarListaSimilaresGlobal(resultados.slice(0, 15));
}

function mostrarListaSimilaresGlobal(lista) {
  document.getElementById("cargandoSimilaresGlobal").style.display = "none";
  document.getElementById("resultadoSimilaresGlobal").style.display = "block";
  document.getElementById("porcentajeGlobalTop").textContent = lista.length
    ? lista[0].promedio.toFixed(2) + "%"
    : "sin resultados";

  const contenedor = document.getElementById("listaSimilaresGlobal");
  contenedor.innerHTML = "";

  lista.forEach((item, i) => {
    const el = document.createElement("div");
    el.className = "similar-item";
    el.dataset.candleId = item.candle.id;
    el.dataset.pct = item.promedio;
    el.innerHTML = `
      <div class="sim-top">
        <span>#${i + 1} — ${item.candle.symbol} ${item.candle.timeframe}</span>
        <span class="sim-pct">${item.promedio.toFixed(2)}% dif. · <span class="sim-estado sim-estado-${item.estado.replace(/\s+/g, "-")}">${item.estado}</span></span>
      </div>
      <span class="sim-fecha">${item.candle.timestamp.replace("T", " ").slice(0, 16)}</span>
    `;
    contenedor.appendChild(el);
  });
}

document.getElementById("listaSimilaresGlobal").addEventListener("click", (evento) => {
  const item = evento.target.closest(".similar-item");
  if (item) activarComparacion(item.dataset.candleId, Number(item.dataset.pct));
});

document.getElementById("btnSimilaresGlobal").addEventListener("click", buscarSimilaresGlobal);

// ---------- Modo comparación: superponer dos velas por posición relativa ----------

const REF_EPOCH = Date.UTC(2000, 0, 1) / 1000; // ancla fija, no representa una fecha real
const seg_por_vela = 15 * 60; // M15

function formatearOffset(time) {
  const offset = Math.round((time - REF_EPOCH) / seg_por_vela);
  return offset === 0 ? "●" : offset > 0 ? "+" + offset : String(offset);
}

function serieRelativa(rango, velaCentro, clave, esVariable) {
  const centroIdx = rango.findIndex((c) => c.timestamp === velaCentro.timestamp);
  if (centroIdx === -1) return [];

  return rango.map((c, i) => ({
    time: REF_EPOCH + (i - centroIdx) * seg_por_vela,
    value: esVariable ? c.variables[clave] : c[clave],
  }));
}

function serieRelativaPrecioNormalizado(rango, velaCentro) {
  const centroIdx = rango.findIndex((c) => c.timestamp === velaCentro.timestamp);
  if (centroIdx === -1) return [];
  const closeCentro = rango[centroIdx].close;

  return rango.map((c, i) => ({
    time: REF_EPOCH + (i - centroIdx) * seg_por_vela,
    value: ((c.close - closeCentro) / closeCentro) * 100,
  }));
}

// Alinea las velas de un candidato ("B") sobre el eje de tiempo real del
// gráfico principal ("A"), usando la posición relativa a cada vela central,
// para poder dibujarlas como una serie de velas superpuesta y semitransparente.
// La vela que coincide exactamente con el centro del candidato (offset 0, es
// decir la vela "más similar" real) se pinta en rojo sólido para distinguirla
// del resto de velas de contexto que la rodean.
function datosVelasSuperpuestas(rangoA, velaCentroA, rangoB, velaCentroB) {
  const centroIdxA = rangoA.findIndex((c) => c.timestamp === velaCentroA.timestamp);
  const centroIdxB = rangoB.findIndex((c) => c.timestamp === velaCentroB.timestamp);
  if (centroIdxA === -1 || centroIdxB === -1) return [];

  const antes = Math.min(centroIdxA, centroIdxB);
  const despues = Math.min(rangoA.length - 1 - centroIdxA, rangoB.length - 1 - centroIdxB);

  const datos = [];
  for (let offset = -antes; offset <= despues; offset++) {
    const a = rangoA[centroIdxA + offset];
    const b = rangoB[centroIdxB + offset];
    if (!a || !b) continue;
    const punto = {
      time: Math.floor(new Date(a.timestamp).getTime() / 1000),
      open: b.open,
      high: b.high,
      low: b.low,
      close: b.close,
    };

    if (offset === 0) {
      // Esta es la vela central del punto similar: la que realmente coincidió.
      punto.color = "rgba(239, 68, 68, 0.85)";
      punto.borderColor = "#EF4444";
      punto.wickColor = "#EF4444";
    }

    datos.push(punto);
  }
  return datos;
}

// Alinea la variable de un rango "origen" (por ejemplo, el punto similar) sobre
// el eje de tiempo real de un rango "referencia" (el gráfico principal), usando
// la posición relativa a cada vela central. Así ambas líneas quedan en el mismo eje X.
function datosSerieIndicadorAlineada(rangoOrigen, velaCentroOrigen, clave, esVariable, rangoRef, velaCentroRef) {
  const centroIdxOrigen = rangoOrigen.findIndex((c) => c.timestamp === velaCentroOrigen.timestamp);
  const centroIdxRef = rangoRef.findIndex((c) => c.timestamp === velaCentroRef.timestamp);
  if (centroIdxOrigen === -1 || centroIdxRef === -1) return [];

  const antes = Math.min(centroIdxOrigen, centroIdxRef);
  const despues = Math.min(rangoOrigen.length - 1 - centroIdxOrigen, rangoRef.length - 1 - centroIdxRef);

  const datos = [];
  for (let offset = -antes; offset <= despues; offset++) {
    const origen = rangoOrigen[centroIdxOrigen + offset];
    const ref = rangoRef[centroIdxRef + offset];
    if (!origen || !ref) continue;
    datos.push({
      time: Math.floor(new Date(ref.timestamp).getTime() / 1000),
      value: esVariable ? origen.variables[clave] : origen[clave],
    });
  }
  return datos;
}

const chartsComparacion = {};

let overlaySeriesDetalle = null; // serie de velas superpuestas sobre chartDetalle
let ultimaComparacion = null;    // { candidata, rangoB } de la última comparación activada

// Indicadores del punto similar (en rojo), dibujados sobre el gráfico principal
// o sobre los sub-gráficos existentes (RSI/ATR/Volume).
const indicadoresActivosSimilar = {};

function quitarIndicadorSimilar(nombre) {
  const info = indicadoresActivosSimilar[nombre];
  if (!info) return;
  const { chart } = chartsPorContenedor[info.containerId] || {};
  if (chart) {
    for (const serie of info.series) chart.removeSeries(serie);
  }
  delete indicadoresActivosSimilar[nombre];
}

function requiereComparacionActiva(checkboxEl) {
  if (ultimaComparacion) return true;
  checkboxEl.checked = false;
  alert("Primero selecciona un punto similar (en 'Buscar puntos similares') para poder comparar sus indicadores.");
  return false;
}

function agregarBBSimilar() {
  const { chart } = chartsPorContenedor["chartDetalle"];
  const up = chart.addLineSeries({ color: "#FCA5A5", lineWidth: 1, priceLineVisible: false });
  const mid = chart.addLineSeries({ color: "#EF4444", lineWidth: 1, priceLineVisible: false });
  const dw = chart.addLineSeries({ color: "#FCA5A5", lineWidth: 1, priceLineVisible: false });
  up.setData(datosSerieIndicadorAlineada(ultimaComparacion.rangoB, ultimaComparacion.candidata, "BB_UP", true, rangoVelasDetalle, velaDetalleActual));
  mid.setData(datosSerieIndicadorAlineada(ultimaComparacion.rangoB, ultimaComparacion.candidata, "BB_MID", true, rangoVelasDetalle, velaDetalleActual));
  dw.setData(datosSerieIndicadorAlineada(ultimaComparacion.rangoB, ultimaComparacion.candidata, "BB_DW", true, rangoVelasDetalle, velaDetalleActual));
  indicadoresActivosSimilar.BB = { containerId: "chartDetalle", series: [up, mid, dw] };
}

function agregarMediaMovilSimilar(nombre, clave, color) {
  const { chart } = chartsPorContenedor["chartDetalle"];
  const serie = chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false });
  serie.setData(datosSerieIndicadorAlineada(ultimaComparacion.rangoB, ultimaComparacion.candidata, clave, true, rangoVelasDetalle, velaDetalleActual));
  indicadoresActivosSimilar[nombre] = { containerId: "chartDetalle", series: [serie] };
}

function actualizarCardRSI() {
  document.getElementById("cardRSI").style.display = document.getElementById("chkRSI").checked ? "block" : "none";
}

function actualizarCardATR() {
  document.getElementById("cardATR").style.display = document.getElementById("chkATR").checked ? "block" : "none";
}

function actualizarCardVolume() {
  document.getElementById("cardVolume").style.display = document.getElementById("chkVolume").checked ? "block" : "none";
}

// Vuelve a dibujar, con los datos del nuevo candidato, todos los indicadores
// del punto similar que ya estuvieran activados al cambiar de comparación.
function refrescarIndicadoresSimilares() {
  if (document.getElementById("chkSuperponer").checked) agregarOverlayVelas();
  if (document.getElementById("chkBBSimilar").checked) {
    quitarIndicadorSimilar("BB");
    agregarBBSimilar();
  }
  if (document.getElementById("chkEMA21Similar").checked) {
    quitarIndicadorSimilar("EMA21");
    agregarMediaMovilSimilar("EMA21", "EMA_21", "#F472B6");
  }
  if (document.getElementById("chkSMMA21Similar").checked) {
    quitarIndicadorSimilar("SMMA21");
    agregarMediaMovilSimilar("SMMA21", "SMMA_21", "#EF4444");
  }
  if (document.getElementById("chkSMMA50Similar").checked) {
    quitarIndicadorSimilar("SMMA50");
    agregarMediaMovilSimilar("SMMA50", "SMMA_50", "#F97316");
  }
  if (document.getElementById("chkSMMA200Similar").checked) {
    quitarIndicadorSimilar("SMMA200");
    agregarMediaMovilSimilar("SMMA200", "SMMA_200", "#A855F7");
  }
}

function crearChartComparacion(containerId, priceFormat) {
  if (chartsComparacion[containerId]) return chartsComparacion[containerId];

  const chart = LightweightCharts.createChart(document.getElementById(containerId), {
    height: 150,
    layout: { background: { color: "#FFFFFF" }, textColor: "#222222" },
    grid: { vertLines: { color: "#EAEAEA" }, horzLines: { color: "#EAEAEA" } },
    timeScale: { tickMarkFormatter: formatearOffset },
    localization: { timeFormatter: formatearOffset },
    rightPriceScale: { borderColor: "#CCCCCC" },
  });

  // priceFormat opcional: se usa para el ATR (5 decimales, tipo 0.00000).
  const opcionesSerie = { lineWidth: 2, priceLineVisible: false };
  if (priceFormat) opcionesSerie.priceFormat = priceFormat;

  const serieActual = chart.addLineSeries({ color: "#2DD4BF", ...opcionesSerie });
  const serieComparada = chart.addLineSeries({ color: "#F59E0B", ...opcionesSerie });

  chartsComparacion[containerId] = { chart, serieActual, serieComparada };
  return chartsComparacion[containerId];
}

function dibujarComparacionEn(containerId, datosActual, datosComparado, priceFormat) {
  const { chart, serieActual, serieComparada } = crearChartComparacion(containerId, priceFormat);
  serieActual.setData(datosActual);
  serieComparada.setData(datosComparado);
  chart.timeScale().fitContent();
}

// Dibuja las velas del punto similar sobre el gráfico principal (chartDetalle),
// en colores más opacos, para comparar visualmente ambas formaciones. La vela
// central (el punto similar real) se resalta en rojo dentro de datosVelasSuperpuestas.
function agregarOverlayVelas() {
  if (!ultimaComparacion || !velaDetalleActual) return;
  quitarOverlayVelas();

  const { chart } = chartsPorContenedor["chartDetalle"];
  overlaySeriesDetalle = chart.addCandlestickSeries({
    upColor: "rgba(245, 197, 24, 0.35)",
    downColor: "rgba(45, 212, 191, 0.35)",
    borderUpColor: "rgba(245, 197, 24, 0.6)",
    borderDownColor: "rgba(45, 212, 191, 0.6)",
    wickUpColor: "rgba(245, 197, 24, 0.5)",
    wickDownColor: "rgba(45, 212, 191, 0.5)",
    priceFormat: { type: "price", precision: 5, minMove: 0.00001 },
  });

  const datos = datosVelasSuperpuestas(
    rangoVelasDetalle,
    velaDetalleActual,
    ultimaComparacion.rangoB,
    ultimaComparacion.candidata
  );
  overlaySeriesDetalle.setData(datos);
}

function quitarOverlayVelas() {
  const { chart } = chartsPorContenedor["chartDetalle"] || {};
  if (chart && overlaySeriesDetalle) {
    chart.removeSeries(overlaySeriesDetalle);
    overlaySeriesDetalle = null;
  }
}

async function activarComparacion(candleId, porcentaje) {
  const { data: candidata, error } = await supabaseClient
    .from("candles")
    .select("*")
    .eq("id", candleId)
    .maybeSingle();

  if (error || !candidata) {
    alert("No se pudo cargar la vela a comparar: " + (error ? error.message : "no encontrada"));
    return;
  }

  const rangoA = rangoVelasDetalle;
  const rangoB = await traerRangoVelas(candidata);

  ultimaComparacion = { candidata, rangoB };
  refrescarIndicadoresSimilares();

  document.getElementById("comparacionContainer").style.display = "block";
  document.getElementById("infoComparacion").textContent =
    `Comparando con ${candidata.symbol} ${candidata.timeframe} — ${candidata.timestamp.replace("T", " ").slice(0, 16)} (dif: ${porcentaje.toFixed(2)}%)`;

  dibujarComparacionEn(
    "chartCompPrecio",
    serieRelativaPrecioNormalizado(rangoA, velaDetalleActual),
    serieRelativaPrecioNormalizado(rangoB, candidata)
  );
  dibujarComparacionEn(
    "chartCompRSI",
    serieRelativa(rangoA, velaDetalleActual, "RSI", true),
    serieRelativa(rangoB, candidata, "RSI", true)
  );
  dibujarComparacionEn(
    "chartCompVolume",
    serieRelativa(rangoA, velaDetalleActual, "volume", false),
    serieRelativa(rangoB, candidata, "volume", false)
  );
  dibujarComparacionEn(
    "chartCompATR",
    serieRelativa(rangoA, velaDetalleActual, "ATR", true),
    serieRelativa(rangoB, candidata, "ATR", true),
    { type: "price", precision: 5, minMove: 0.00001 }
  );

  document.getElementById("comparacionContainer").scrollIntoView({ behavior: "smooth" });
}

document.getElementById("btnQuitarComparacion").addEventListener("click", () => {
  document.getElementById("comparacionContainer").style.display = "none";
  quitarOverlayVelas();
  document.getElementById("chkSuperponer").checked = false;

  for (const nombre of Object.keys(indicadoresActivosSimilar)) quitarIndicadorSimilar(nombre);
  [
    "chkBBSimilar", "chkEMA21Similar", "chkSMMA21Similar", "chkSMMA50Similar", "chkSMMA200Similar",
  ].forEach((id) => {
    document.getElementById(id).checked = false;
  });

  ultimaComparacion = null;
});

document.getElementById("chkSuperponer").addEventListener("change", (e) => {
  if (e.target.checked) {
    agregarOverlayVelas();
  } else {
    quitarOverlayVelas();
  }
});

document.getElementById("puntosContenedor").addEventListener("click", (evento) => {
  const item = evento.target.closest(".punto-item");
  if (item) verDetallePunto(item.dataset.candleId, item.dataset.resultado);
});

document.getElementById("btnBuscar").addEventListener("click", buscarVela);
document.getElementById("btnVerPuntos").addEventListener("click", verPuntosMarcados);
document.getElementById("btnBuyODetalle").addEventListener("click", () => marcarPunto("buy", "bueno"));
document.getElementById("btnBuyXDetalle").addEventListener("click", () => marcarPunto("buy", "malo"));
document.getElementById("btnSellODetalle").addEventListener("click", () => marcarPunto("sell", "bueno"));
document.getElementById("btnSellXDetalle").addEventListener("click", () => marcarPunto("sell", "malo"));
document.getElementById("btnSimilarDetalle").addEventListener("click", buscarPuntoSimilar);

// ---------- Calculadora: pegar variables sueltas (sin fecha) y buscar el punto más parecido ----------

// Cada etiqueta posible (con o sin espacio/guión bajo, con o sin ":") se
// traduce a su nombre canónico en la tabla. Todo lo que no esté aquí se
// ignora (SYMBOL, TIMEFRAME, DATE, o cualquier línea en blanco/desconocida).
const ALIAS_CALCULADORA = {
  OPEN: "OPEN", HIGH: "HIGH", LOW: "LOW", CLOSE: "CLOSE", VOLUME: "VOLUME",
  RSI: "RSI", ATR: "ATR",
  EMA_21: "EMA_21", EMA21: "EMA_21",
  SMMA_21: "SMMA_21", SMMA21: "SMMA_21",
  SMMA_50: "SMMA_50", SMMA50: "SMMA_50",
  SMMA_200: "SMMA_200", SMMA200: "SMMA_200",
  MACD_MAIN: "MACD_MAIN", MACDMAIN: "MACD_MAIN",
  MACD_SIGNAL: "MACD_SIGNAL", MACDSIGNAL: "MACD_SIGNAL",
  BB_UP: "BB_UP", BB_UPPER: "BB_UP", BBUP: "BB_UP", BBUPPER: "BB_UP",
  BB_MID: "BB_MID", BB_MIDDLE: "BB_MID", BBMID: "BB_MID", BBMIDDLE: "BB_MID",
  BB_DW: "BB_DW", BB_LOWER: "BB_DW", BB_DOWN: "BB_DW", BBDW: "BB_DW", BBLOWER: "BB_DW", BBDOWN: "BB_DW",
};

function parsearTextoCalculadora(texto) {
  const valores = {};
  const lineas = texto.split("\n");

  for (const lineaCruda of lineas) {
    const linea = lineaCruda.trim();
    if (!linea) continue;

    const idxDosPuntos = linea.indexOf(":");
    let etiquetaCruda, valorCrudo;

    if (idxDosPuntos !== -1) {
      // Formato "ETIQUETA: valor" (con espacios permitidos en la etiqueta,
      // ej. "MACD MAIN: 0.00059" o "BB UPPER: 0.71026").
      etiquetaCruda = linea.slice(0, idxDosPuntos);
      valorCrudo = linea.slice(idxDosPuntos + 1);
    } else {
      // Formato antiguo, pegado sin ":" (ej. "OPEN0.70391").
      const coincidencia = linea.match(/^([A-Za-z_ ]+)(-?[\d.].*)$/);
      if (!coincidencia) continue;
      etiquetaCruda = coincidencia[1];
      valorCrudo = coincidencia[2];
    }

    const etiqueta = etiquetaCruda.trim().toUpperCase().replace(/[\s_]+/g, "_");
    const campo = ALIAS_CALCULADORA[etiqueta];
    if (!campo) continue; // SYMBOL, TIMEFRAME, DATE u otra etiqueta desconocida

    const numero = parseFloat(valorCrudo.trim());
    if (!Number.isNaN(numero)) valores[campo] = numero;
  }

  return valores;
}

// Misma lógica que calcular_variables.py, traducida a JS: cuerpo/sombras de la
// vela y distancias (en pips, x10000) a las medias móviles y a las Bollinger.
function calcularVariablesDerivadas(v) {
  const C = v.OPEN, D = v.HIGH, E = v.LOW, F = v.CLOSE;
  const L = v.BB_UP, M = v.BB_MID, N = v.BB_DW;
  const O = v.SMMA_21, P = v.SMMA_50, Q = v.SMMA_200;

  const bodySize = (F - C) * 10000;
  const upperShadow = (bodySize > 0 ? D - F : C - D) * 10000;
  const lowerShadow = (bodySize > 0 ? C - E : F - E) * 10000;
  const fullSize = Math.abs(bodySize) + Math.abs(upperShadow) + Math.abs(lowerShadow);

  const r4 = (n) => Math.round(n * 10000) / 10000;

  return {
    BODY_SIZE: r4(bodySize),
    UPPER_SHADOW: r4(upperShadow),
    LOWER_SHADOW: r4(lowerShadow),
    FULL_SIZE: r4(fullSize),

    "OPEN-200": r4((C - Q) * 10000),
    "OPEN-50": r4((C - P) * 10000),
    "OPEN-21": r4((C - O) * 10000),
    "OPEN-BBUP": r4((C - L) * 10000),
    "OPEN-BBMID": r4((C - M) * 10000),
    "OPEN-BBDW": r4((C - N) * 10000),

    "CLOSE-200": r4((F - Q) * 10000),
    "CLOSE-50": r4((F - P) * 10000),
    "CLOSE-21": r4((F - O) * 10000),
    "CLOSE-BBUP": r4((F - L) * 10000),
    "CLOSE-BBMID": r4((F - M) * 10000),
    "CLOSE-BBDW": r4((F - N) * 10000),

    "HIGH-200": r4((D - Q) * 10000),
    "HIGH-50": r4((D - P) * 10000),
    "HIGH-21": r4((D - O) * 10000),
    "HIGH-BBUP": r4((D - L) * 10000),
    "HIGH-BBMID": r4((D - M) * 10000),
    "HIGH-BBDW": r4((D - N) * 10000),

    "LOW-200": r4((E - Q) * 10000),
    "LOW-50": r4((E - P) * 10000),
    "LOW-21": r4((E - O) * 10000),
    "LOW-BBUP": r4((E - L) * 10000),
    "LOW-BBMID": r4((E - M) * 10000),
    "LOW-BBDW": r4((E - N) * 10000),
  };
}

async function calcularYBuscarSimilar() {
  const mensaje = document.getElementById("mensajeCalculadora");
  const resultado = document.getElementById("resultadoCalculadora");
  mensaje.textContent = "";
  resultado.style.display = "none";

  const texto = document.getElementById("calcTexto").value;
  const valoresBase = parsearTextoCalculadora(texto);

  const camposRequeridos = [
    "OPEN", "HIGH", "LOW", "CLOSE",
    "RSI", "ATR", "EMA_21", "SMMA_21", "SMMA_50", "SMMA_200",
    "MACD_MAIN", "MACD_SIGNAL", "BB_UP", "BB_MID", "BB_DW",
  ];
  const faltantes = camposRequeridos.filter((c) => valoresBase[c] === undefined);
  if (faltantes.length > 0) {
    mensaje.textContent = "Faltan estos datos: " + faltantes.join(", ");
    return;
  }

  const derivadas = calcularVariablesDerivadas(valoresBase);
  const variablesCompletas = { ...valoresBase, ...derivadas };
  delete variablesCompletas.OPEN;
  delete variablesCompletas.HIGH;
  delete variablesCompletas.LOW;
  delete variablesCompletas.CLOSE;
  delete variablesCompletas.VOLUME; // estas van aparte, no forman parte de "variables"

  mensaje.textContent = "Buscando puntos parecidos…";

  const { data: todasLasVelas, error: errorVelas } = await supabaseClient
    .from("candles")
    .select("variables");

  if (errorVelas) {
    mensaje.textContent = "Error trayendo velas para normalizar: " + errorVelas.message;
    return;
  }

  const minMax = {};
  for (const fila of todasLasVelas) {
    for (const [clave, valor] of Object.entries(fila.variables || {})) {
      if (valor === null || valor === undefined) continue;
      if (!minMax[clave]) minMax[clave] = [valor, valor];
      minMax[clave][0] = Math.min(minMax[clave][0], valor);
      minMax[clave][1] = Math.max(minMax[clave][1], valor);
    }
  }

  const { data: candidatas, error: errorCand } = await supabaseClient
    .from("candles")
    .select("id, symbol, timeframe, timestamp, variables");

  if (errorCand) {
    mensaje.textContent = "Error trayendo velas: " + errorCand.message;
    return;
  }

  // Resultado (si existe) de los puntos ya marcados, para mostrar si cada
  // vela parecida fue exitosa, fallida, o todavía no se evaluó.
  const { data: puntosExistentes, error: errorPuntos } = await supabaseClient
    .from("points")
    .select("candle_id, resultado");

  if (errorPuntos) {
    mensaje.textContent = "Error trayendo puntos: " + errorPuntos.message;
    return;
  }

  const resultadoPorCandleId = new Map();
  for (const p of puntosExistentes) {
    resultadoPorCandleId.set(p.candle_id, p.resultado || "pendiente");
  }

  const resultados = (candidatas || []).map((c) => {
    let suma = 0;
    let contador = 0;
    for (const clave of Object.keys(minMax)) {
      const v1 = variablesCompletas[clave];
      const v2 = c.variables[clave];
      if (v1 === undefined || v2 === undefined) continue;
      const [min, max] = minMax[clave];
      const rango = max - min || 1;
      suma += (Math.abs(v2 - v1) / rango) * 100;
      contador++;
    }
    const promedio = contador > 0 ? suma / contador : 100;
    return { candle: c, promedio, estado: resultadoPorCandleId.get(c.id) || "no evaluado" };
  });

  resultados.sort((a, b) => a.promedio - b.promedio);
  const top = resultados.slice(0, 15);

  mensaje.textContent = "";

  if (top.length === 0) {
    mensaje.textContent = "No se encontraron velas para comparar.";
    return;
  }

  mostrarResultadoCalculadora(top);
}

function mostrarResultadoCalculadora(lista) {
  document.getElementById("resultadoCalculadora").style.display = "block";
  document.getElementById("porcentajeCalculadora").textContent =
    lista[0].promedio.toFixed(2) + "%" + (lista[0].promedio === 0 ? " (coincidencia exacta)" : "");

  const contenedor = document.getElementById("listaCalculadora");
  contenedor.innerHTML = "";

  lista.forEach((item, i) => {
    const el = document.createElement("div");
    el.className = "similar-item";
    el.dataset.candleId = item.candle.id;
    el.dataset.estado = item.estado;
    el.innerHTML = `
      <div class="sim-top">
        <span>#${i + 1} — ${item.candle.symbol} ${item.candle.timeframe}</span>
        <span class="sim-pct">${item.promedio.toFixed(2)}% dif. · <span class="sim-estado sim-estado-${item.estado.replace(/\s+/g, "-")}">${item.estado}</span></span>
      </div>
      <span class="sim-fecha">${item.candle.timestamp.replace("T", " ").slice(0, 16)}</span>
    `;
    contenedor.appendChild(el);
  });
}

// Al tocar una de las velas parecidas, se abre el mismo detalle completo
// (gráfico + indicadores + variables) que al seleccionar un punto marcado.
document.getElementById("listaCalculadora").addEventListener("click", (evento) => {
  const item = evento.target.closest(".similar-item");
  if (item) verDetallePunto(item.dataset.candleId, item.dataset.estado);
});

document.getElementById("btnAbrirCalculadora").addEventListener("click", () => {
  const calc = document.getElementById("calculadora");
  calc.style.display = calc.style.display === "none" ? "block" : "none";
});
document.getElementById("btnCalcularSimilar").addEventListener("click", calcularYBuscarSimilar);

revisarSesion();
