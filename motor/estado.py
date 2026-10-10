"""
estado.py — construye datos/estado.json: todo lo que la página pública necesita.

Solo librería estándar. Se ejecuta en la EliteBook cada 15 minutos (agentes/actualizar.py)
y también sirve para generar un estado de prueba en la Mac.

Reglas: ninguna predicción; cada cifra lleva fuente y hora de corte; magnitudes USGS;
los textos salen de plantillas fijas rellenadas con números, nunca de un modelo.
"""
from __future__ import annotations
import json, math, os, datetime as dt
from sismo_lib import (MAINSHOCK, PANAMA_BBOX, PANAMA_TZ, RJ_GENERIC, SECTORS, fetch_catalog,
                       events_from_geojson, sequence_metrics, forecast_table, red_flags, local_str,
                       sector_of, rj_expected)

AQUI = os.path.dirname(os.path.abspath(__file__))
SECUENCIAS = json.load(open(os.path.join(AQUI, "secuencias_dobles.json"), encoding="utf-8"))["secuencias"]

INCOMPLETOS = {"Chile 1960", "Salomón 1971", "Denali 2002"}
DIRS = {"N": "norte", "S": "sur", "E": "este", "W": "oeste", "NE": "noreste", "NW": "noroeste", "SE": "sureste", "SW": "suroeste",
        "NNE": "nor-noreste", "ENE": "este-noreste", "ESE": "este-sureste", "SSE": "sur-sureste", "SSW": "sur-suroeste",
        "WSW": "oeste-suroeste", "WNW": "oeste-noroeste", "NNW": "nor-noroeste"}


def lugar_es(place: str | None) -> str:
    """'14 km W of Río Grande, Panama' → '14 km al oeste de Río Grande'."""
    if not place:
        return "sin referencia"
    import re
    mm = re.match(r"^(\d+)\s*km\s+([NSEW]{1,3})\s+of\s+(.+?)(?:,\s*Panama)?$", place)
    if mm:
        return f"{mm.group(1)} km al {DIRS.get(mm.group(2), mm.group(2))} de {mm.group(3)}"
    return place.replace(", Panama", "")


MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha_local(t_ms: int, con_hora: bool = True) -> str:
    d = dt.datetime.fromtimestamp(t_ms / 1000, PANAMA_TZ)
    s = f"{d.day} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][d.month-1]}"
    if con_hora:
        h = d.hour % 12 or 12
        s += f", {h}:{d.minute:02d} {'a. m.' if d.hour < 12 else 'p. m.'}"
    return s


def num(x, dec=1):
    """Formato español: coma decimal."""
    if x is None:
        return "—"
    return f"{x:.{dec}f}".replace(".", ",")


def horas_texto(h: float) -> str:
    if h < 1:
        return f"{int(round(h * 60))} min"
    if h < 48:
        return f"{num(h, 1)} h"
    return f"{num(h / 24, 1)} días"


# ---------------------------------------------------------------------------
# Comparación con las secuencias dobles, en la misma ventana que lleva Panamá
# ---------------------------------------------------------------------------
def metricas_secuencia(pts, Mm, big, mc, T_h):
    """pts: lista [h, m] desde el primer evento. Replica la lógica del paquete original."""
    tb = next((h for h, m in pts if big and m >= big - 0.05), None)
    T = min(T_h, tb) if tb else T_h
    win = [(h, m) for h, m in pts if 0 < h < T]
    obs = sum(1 for h, m in win if m >= mc)
    esp = rj_expected(Mm, mc, 0, T / 24, **RJ_GENERIC)
    mags = [m for _, m in win]
    mitad = T / 2
    m1 = max([m for h, m in win if h <= mitad], default=None)
    m2 = max([m for h, m in win if h > mitad], default=None)
    return dict(Mm=Mm, mayor=big, T_mayor_h=round(tb, 1) if tb else None, ventana_h=round(T, 1), umbral=mc,
                obs=obs, esp=round(esp, 1), razon=round(obs / esp, 2) if esp else None,
                max_mag=max(mags) if mags else None, max_dif=round(max(mags) - Mm, 1) if mags else None,
                max_1a_mitad=m1, max_2a_mitad=m2, escalada=(m1 is not None and m2 is not None and m2 > m1),
                serie=[[h, m] for h, m in win if m >= Mm - 3.2 and h <= 24 * 14])


def comparacion(eventos_pa, now_h):
    pts = [[(e["t"] - MAINSHOCK["time_ms"]) / 3.6e6, e["mag"]] for e in eventos_pa if e["t"] > MAINSHOCK["time_ms"]]
    res = {"Panamá 2026": dict(metricas_secuencia(pts, MAINSHOCK["mag"], None, 5.0, now_h), nota="")}
    for s in SECUENCIAS:
        r = metricas_secuencia(s["events"], s["Mm"], s["big"], s["threshold"], now_h)
        r["nota"] = s["note"]
        res[s["name"]] = r
    return res


def carrera(comp, now_h):
    """Ventanas: cuándo llegó el mayor en cada caso histórico, y si Panamá ya la pasó."""
    v = []
    for s in SECUENCIAS:
        r = comp[s["name"]]
        tb = r["T_mayor_h"]
        if tb is None:
            continue
        v.append(dict(nombre=s["name"], horas=tb, primero=s["Mm"], mayor=s["big"], superada=now_h > tb,
                      texto=horas_texto(tb)))
    v.sort(key=lambda x: x["horas"])
    superadas = sum(1 for x in v if x["superada"])
    prox = next((x for x in v if not x["superada"]), None)
    return dict(ahora_h=round(now_h, 2), ventanas=v, superadas=superadas, total=len(v),
                proxima=prox and dict(nombre=prox["nombre"], horas=prox["horas"], faltan_h=round(prox["horas"] - now_h, 1)))


# ---------------------------------------------------------------------------
# Señales (lo que sí mostraron los casos que terminaron en uno mayor)
# ---------------------------------------------------------------------------
def senales(m, comp):
    malos = {k: v for k, v in comp.items() if k != "Panamá 2026"}
    # Solo casos con catálogo utilizable (Chile 1960, Salomón 1971 y Denali 2002 están incompletos)
    utiles = {k: v for k, v in malos.items() if k not in INCOMPLETOS}
    n_u = len(utiles)
    hiper = sum(1 for v in utiles.values() if v["razon"] and v["razon"] >= 1.5)
    escalaron = sum(1 for v in malos.values() if v["escalada"])
    cerca = sum(1 for v in utiles.values() if v["max_dif"] is not None and abs(v["max_dif"]) <= 0.8)
    out = []

    r = m["ratio_ge5"]
    estado = "rojo" if (r or 0) >= 1.5 else ("ambar" if (r or 0) >= 1.2 else "verde")
    out.append(dict(clave="productividad", pregunta="¿Hay más réplicas de lo normal?",
                    respuesta={"verde": "No", "ambar": "Algo más", "rojo": "Sí"}[estado], estado=estado,
                    panama=f"{num(r, 1)} veces el promedio mundial" if r else "sin datos",
                    casos=f"en {hiper} de {n_u} casos, 1,5 veces o más",
                    detalle=f"{m['n_ge5']} réplicas de magnitud 5 o más frente a {num(m['exp_ge5'], 0)} que el modelo esperaba a esta hora."))

    esc = m["escalation"]
    estado = "rojo" if esc["escalating"] else "verde"
    a, b = esc["max_first_half"], esc["max_second_half"]
    out.append(dict(clave="escalada", pregunta="¿Están creciendo en vez de bajar?",
                    respuesta="Sí" if esc["escalating"] else "No", estado=estado,
                    panama=f"la mayor pasó de {num(a)} a {num(b) if b == b else '—'}" if a == a else "sin datos",
                    casos=f"en {escalaron} de {len(malos)} casos crecieron",
                    detalle="Se compara la réplica más grande de la primera mitad del tiempo transcurrido con la de la segunda mitad."))

    L = m["largest"]
    dif = abs(L["diff"]) if L else None
    estado = "rojo" if dif is not None and dif < 0.8 else ("ambar" if dif is not None and dif < 1.0 else "verde")
    out.append(dict(clave="bath", pregunta="¿La réplica mayor fue demasiado grande?",
                    respuesta={"verde": "No", "ambar": "Al límite", "rojo": "Sí"}[estado], estado=estado,
                    panama=f"{num(L['mag'])} frente a {num(MAINSHOCK['mag'])}: {num(dif)} grados menos" if L else "sin datos",
                    casos=f"en {cerca} de {n_u} casos, 0,8 grados o menos",
                    detalle="Lo normal (ley de Båth) es que la réplica mayor quede 1,2 grados por debajo del principal; menos de 0,8 es señal."))

    puntas = m["sectors_last6h"].get("W_beyond", 0) + m["sectors_last6h"].get("E_beyond", 0)
    estado = "rojo" if puntas >= 3 else ("ambar" if puntas == 2 else "verde")
    out.append(dict(clave="puntas", pregunta="¿Se están moviendo hacia las puntas de la falla?",
                    respuesta={"verde": "No", "ambar": "Un poco", "rojo": "Sí"}[estado], estado=estado,
                    panama=f"{puntas} réplicas en las puntas en las últimas 6 h",
                    casos="Tohoku 2011 migró hacia el segmento vecino antes del 9,1",
                    detalle="Puntas: más allá de Soná y el golfo de Montijo al oeste; mar adentro de Pedasí al este. Tres o más en seis horas sería señal."))
    return out


def semaforo(flags, senal, m):
    rojo_directo = any(e["mag"] >= 6.5 and e.get("sector") in ("W_beyond", "E_beyond") and e.get("h", 0) >= 0 for e in m.get("_recientes", []))
    n = len(flags)
    if n >= 2 or rojo_directo:
        est = "rojo"
    elif n == 1:
        est = "ambar"
    else:
        est = "verde"
    textos = {
        "verde": ("Comportamiento normal",
                  "Las réplicas se comportan como en una secuencia típica: bajan en número y en tamaño. "
                  "Ninguna de las señales que precedieron a un sismo mayor en los casos históricos está presente. "
                  "Eso no significa que no pueda pasar: la probabilidad de uno mayor nunca es cero. Significa que hoy no hay motivo para cambiar de rutina."),
        "ambar": ("Atención: una señal activa",
                  "Una de las señales que se vigilan está activa. No es una predicción ni una alerta oficial; es un motivo para repasar el plan familiar "
                  "y estar pendientes de Sinaproc y del Instituto de Geociencias."),
        "rojo": ("Atención: varias señales activas",
                 "Dos o más señales están activas al mismo tiempo. Sigue siendo imposible predecir un sismo, pero el comportamiento se parece más "
                 "al de las secuencias que terminaron en uno mayor. Revise el plan familiar y siga únicamente las indicaciones de Sinaproc."),
    }
    t, cuerpo = textos[est]
    return dict(estado=est, titulo=t, texto=cuerpo, banderas=flags)


# ---------------------------------------------------------------------------
# Boletín automático (plantilla)
# ---------------------------------------------------------------------------
def boletin(m, comp, carr, pron, sem, generado_ms, ultimas):
    L = m["largest"]
    p7 = next(r for r in pron if r["window"] == "7 días")
    fit, gen = p7.get("fit") or p7["gen"], p7["gen"]
    pr = sorted([fit["p_ge_main"], gen["p_ge_main"]])
    p6 = sorted([fit["p_ge6"], gen["p_ge6"]])
    uno_en = int(round(1 / pr[1])) if pr[1] > 0 else None
    parrafos = [
        f"Corte: {fecha_local(generado_ms)}, {horas_texto(m['now_h'])} después del sismo principal (magnitud 7,7 USGS, 9 de octubre, 12:56 p. m.). "
        f"El USGS tiene registradas {m['n_aftershocks']} réplicas de magnitud 2,5 o más; {m['n_ge5']} de ellas de magnitud 5 o más "
        f"y {m['n_ge6']} de magnitud 6 o más.",
        (f"La réplica mayor sigue siendo la de magnitud {num(L['mag'])} ({fecha_local(MAINSHOCK['time_ms'] + int(L['h'] * 3.6e6))}, {lugar_es(L['place'])}). "
         f"Después de ella la mayor ha sido de {num(m['max_after_largest'])}. "
         f"Han pasado {horas_texto(m['hours_since_last_ge5'])} desde la última réplica de magnitud 5 o más.") if L else "",
        f"Comparación con los once casos históricos en que el primer sismo fue seguido por uno mayor: Panamá ya pasó {carr['superadas']} de {carr['total']} ventanas"
        + (f"; la próxima es la de {carr['proxima']['nombre']} ({horas_texto(carr['proxima']['horas'])}), que se cierra en {horas_texto(carr['proxima']['faltan_h'])}."
           if carr["proxima"] else ". Todas las ventanas de los casos históricos quedaron atrás."),
        f"Señales: {', '.join(s['pregunta'].rstrip('?').lstrip('¿') + ': ' + s['respuesta'].lower() for s in sem['senales'])}. Estado general: {sem['titulo'].lower()}.",
        f"Qué esperar en los próximos 7 días según el modelo estadístico (no es una predicción): entre {num(fit['n_ge5'], 0)} y {num(gen['n_ge5'], 0)} réplicas de magnitud 5 o más; "
        f"probabilidad de al menos una de magnitud 6 o más entre {int(p6[0]*100)} % y {int(p6[1]*100)} %; "
        f"probabilidad de un sismo mayor que el 7,7 entre {num(pr[0]*100, 1)} % y {num(pr[1]*100, 1)} %"
        + (f" (como mucho, 1 de cada {uno_en})." if uno_en else "."),
    ]
    if ultimas:
        u = ultimas[0]
        parrafos.append(f"Última réplica registrada por el USGS: magnitud {num(u['mag'])}, {fecha_local(u['t'])}, {u['lugar']}"
                        + (f"; {u['sentida']} persona{'s' if u['sentida'] != 1 else ''} {'reportaron' if u['sentida'] != 1 else 'reportó'} haberla sentido al USGS." if u.get("sentida") else "."))
    return dict(fecha=fecha_local(generado_ms), parrafos=[p for p in parrafos if p])


# ---------------------------------------------------------------------------
def construir(gj: dict, generado_ms: int | None = None) -> dict:
    generado_ms = generado_ms or gj["metadata"]["generated"]
    eventos = events_from_geojson(gj)
    # campos extra: sentido / cdi (Did You Feel It)
    extra = {f["id"]: f["properties"] for f in gj["features"]}
    t0, Mm = MAINSHOCK["time_ms"], MAINSHOCK["mag"]
    m = sequence_metrics(eventos, Mm, t0, generado_ms)
    a_fit = m["a_fitted"] if m["a_fitted"] == m["a_fitted"] else None
    pron = forecast_table(Mm, m["now_h"] / 24, RJ_GENERIC, a_fit)
    flags = red_flags(m, Mm)
    comp = comparacion(eventos, m["now_h"])
    carr = carrera(comp, m["now_h"])
    sen = senales(m, comp)

    aft = [e for e in eventos if e["t"] > t0]
    ev_out = []
    for e in aft:
        p = extra.get(e["id"], {})
        ev_out.append(dict(id=e["id"], t=e["t"], local=local_str(e["t"], "%d-%b %H:%M"),
                           h=round((e["t"] - t0) / 3.6e6, 2), mag=e["mag"], lat=e["lat"], lon=e["lon"],
                           prof=round(e["depth"] or 0, 1), lugar=lugar_es(e["place"]), sector=sector_of(e["lon"]),
                           sentida=p.get("felt") or 0, cdi=p.get("cdi")))
    m["_recientes"] = [e for e in ev_out if (generado_ms - e["t"]) / 3.6e6 <= 6]
    sem = semaforo(flags, sen, m)
    sem["senales"] = sen
    del m["_recientes"]
    ultimas = sorted(ev_out, key=lambda e: -e["t"])[:5]
    bol = boletin(m, comp, carr, pron, sem, generado_ms, ultimas)

    return dict(
        version=1,
        generado_utc=dt.datetime.fromtimestamp(generado_ms / 1000, dt.timezone.utc).isoformat(timespec="seconds"),
        generado_ms=generado_ms,
        generado_local=fecha_local(generado_ms),
        principal=dict(MAINSHOCK, fecha_local=fecha_local(t0), mag_igc=7.2),
        ahora_h=m["now_h"],
        semaforo=sem,
        carrera=carr,
        metricas=m,
        pronostico=pron,
        comparacion=dict(ventana_h=round(m["now_h"], 1), secuencias=comp),
        eventos=ev_out,
        boletin=bol,
        sectores={k: d for k, _, _, d in SECTORS},
        fuente="USGS NEIC ComCat (servicio FDSN). Magnitudes USGS; las del IGC-UP corren 0,3–0,7 por encima en esta secuencia.",
        aviso="Recicladora Nacional de Panamá no es autoridad sismológica. Para emergencias: Sinaproc 520-4426 / 911. Reportes oficiales: Instituto de Geociencias de la Universidad de Panamá.",
    )


def descargar() -> dict:
    return fetch_catalog("2026-10-09T17:00:00", None, minmagnitude=2.5, bbox=PANAMA_BBOX)


if __name__ == "__main__":
    import sys
    gj = json.load(open(sys.argv[1])) if len(sys.argv) > 1 and os.path.exists(sys.argv[1]) else descargar()
    est = construir(gj)
    salida = sys.argv[2] if len(sys.argv) > 2 else os.path.join(AQUI, "..", "datos", "estado.json")
    json.dump(est, open(salida, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(est["generado_local"], "|", est["semaforo"]["estado"], "|", est["carrera"]["superadas"], "/", est["carrera"]["total"], "| bytes:", os.path.getsize(salida))
    for p in est["boletin"]["parrafos"]:
        print("·", p)
