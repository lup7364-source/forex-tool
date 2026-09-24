const SUPABASE_URL = "https://zbndbecsqhzfmblgpgiw.supabase.co";
const SUPABASE_ANON_KEY = "PEGA_AQUI_TU_ANON_KEY";

const supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

let velaActual = null; // guarda la vela encontrada mientras el usuario decide marcarla

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

  const resultado = document.getElementById("resultado");
  const infoVela = document.getElementById("infoVela");
  const mensajeMarcado = document.getElementById("mensajeMarcado");
  mensajeMarcado.textContent = "";

  if (!symbol || !timeframe || !fecha || hora === "") {
    alert("Completa símbolo, timeframe, fecha y hora antes de buscar.");
    return;
  }

  const timestampInput = `${fecha}T${hora}:${minuto}:00`;

  const timestampISO = `${timestampInput}Z`; // se usa tal cual, sin reinterpretar como hora local

  const { data, error } = await supabaseClient
    .from("candles")
    .select("*")
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
    resultado.style.display = "none";
    alert("No se encontró ninguna vela con esos datos exactos.");
    return;
  }

  velaActual = data;

  infoVela.textContent =
    `${data.symbol} ${data.timeframe} — ${data.timestamp.replace("T", " ").slice(0, 16)} | ` +
    `O:${data.open} H:${data.high} L:${data.low} C:${data.close}`;

  resultado.style.display = "block";
  dibujarGraficoAlrededorDe(data);
}

async function marcarPunto(direccion, tipo) {
  if (!velaActual) return;

  const nota = document.getElementById("nota").value.trim();
  const mensajeMarcado = document.getElementById("mensajeMarcado");

  const { error } = await supabaseClient.from("points").insert({
    candle_id: velaActual.id,
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

let chart = null;
let serieVelas = null;

function inicializarChart() {
  if (chart) return; // ya existe, no crear otro

  chart = LightweightCharts.createChart(document.getElementById("chart"), {
    height: 260,
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

  serieVelas = chart.addCandlestickSeries({
    upColor: "#FFFFFF",
    downColor: "#000000",
    borderUpColor: "#000000",
    borderDownColor: "#000000",
    wickUpColor: "#000000",
    wickDownColor: "#000000",
  });
}

async function dibujarGraficoAlrededorDe(vela) {
  inicializarChart();

  const RANGO_VELAS = 40; // velas antes y después
  const MS_POR_VELA = 15 * 60 * 1000; // M15

  const centro = new Date(vela.timestamp);
  const desde = new Date(centro.getTime() - RANGO_VELAS * MS_POR_VELA).toISOString();
  const hasta = new Date(centro.getTime() + RANGO_VELAS * MS_POR_VELA).toISOString();

  const { data, error } = await supabaseClient
    .from("candles")
    .select("timestamp, open, high, low, close")
    .eq("symbol", vela.symbol)
    .eq("timeframe", vela.timeframe)
    .gte("timestamp", desde)
    .lte("timestamp", hasta)
    .order("timestamp", { ascending: true });

  if (error) {
    console.error("Error cargando el gráfico:", error);
    return;
  }

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

  serieVelas.setData(datos);
  chart.timeScale().fitContent();
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
  if (!velaActual) return;

  document.getElementById("cargandoSimilar").style.display = "block";
  document.getElementById("resultadoSimilar").style.display = "none";

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
    document.getElementById("cargandoSimilar").style.display = "none";
    alert("Todavía no hay puntos marcados para comparar.");
    return;
  }

  // 3. Calcular el % de diferencia de cada punto marcado contra la vela actual
  const actual = velaActual.variables || {};

  const candidatos = puntos
    .filter((p) => p.candles && p.candles.id !== velaActual.id) // no comparar contra sí misma
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
  document.getElementById("cargandoSimilar").style.display = "none";
  const resultadoSimilar = document.getElementById("resultadoSimilar");
  resultadoSimilar.style.display = "block";

  const c = mejor.punto.candles;
  document.getElementById("infoSimilar").textContent =
    `${c.symbol} ${c.timeframe} — ${c.timestamp.replace("T", " ").slice(0, 16)} | ` +
    `Tipo: ${mejor.punto.tipo.toUpperCase()}` +
    (mejor.punto.nota ? ` | Nota: ${mejor.punto.nota}` : "");

  document.getElementById("porcentajeTotal").textContent =
    mejor.promedio.toFixed(2) + "%" + (mejor.promedio === 0 ? " (coincidencia exacta)" : "");

  const cuerpo = document.querySelector("#tablaVariables tbody");
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

document.getElementById("btnBuscar").addEventListener("click", buscarVela);
document.getElementById("btnBuyO").addEventListener("click", () => marcarPunto("buy", "bueno"));
document.getElementById("btnBuyX").addEventListener("click", () => marcarPunto("buy", "malo"));
document.getElementById("btnSellO").addEventListener("click", () => marcarPunto("sell", "bueno"));
document.getElementById("btnSellX").addEventListener("click", () => marcarPunto("sell", "malo"));
document.getElementById("btnSimilar").addEventListener("click", buscarPuntoSimilar);

revisarSesion();
