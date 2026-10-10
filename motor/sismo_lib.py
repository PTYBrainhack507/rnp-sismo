"""
sismo_lib.py — funciones compartidas del monitor de réplicas (terremoto de Azuero, 9-oct-2026).

Dependencias: solo la librería estándar (urllib, json, math, datetime).
Zona horaria de Panamá: UTC-5 (sin horario de verano).

Secciones:
  1. Descarga de catálogos USGS (FDSN event web service)
  2. Modelo de réplicas Reasenberg–Jones (1989) + ley de Omori + ley de Båth
  3. Métricas de una secuencia (conteos por ventana, razón obs/esp, escalada, sectores)
"""
from __future__ import annotations
import json, math, urllib.request, urllib.parse, datetime as dt

USGS_FDSN = "https://earthquake.usgs.gov/fdsnws/event/1/query"
PANAMA_TZ = dt.timezone(dt.timedelta(hours=-5))

# ---------------------------------------------------------------------------
# Evento principal (USGS, revisado)
# ---------------------------------------------------------------------------
MAINSHOCK = {
    "id": "us6000u18k",
    "time_ms": 1791568566036,          # 2026-10-09 17:56:06.036 UTC = 12:56:06 hora de Panamá
    "mag": 7.7, "mag_type": "Mww",
    "lat": 7.5868, "lon": -80.7690, "depth_km": 12.6,
    "place": "12 km WSW of Pitaloza Arriba, Panama",
    "mechanism": {"strike": 288, "dip": 66, "rake": -17, "sense": "sinistral (left-lateral strike-slip)"},
    "finite_fault": {"length_km": 150, "width_km": 35, "max_slip_m": 8.3, "avg_rupture_velocity_km_s": 1.64,
                     "duration_s": 33, "hypocenter_x_km_from_ESE_end": 87.5},
}

# Caja geográfica de la secuencia (toda la falla Azuero-Soná + márgenes)
PANAMA_BBOX = dict(minlatitude=6.0, maxlatitude=9.5, minlongitude=-82.5, maxlongitude=-78.5)

# Sectores a lo largo de la ruptura (longitud). Extremos de la ruptura USGS v1: -81.30 (W) y -80.01 (E).
SECTORS = [
    ("W_beyond", -999, -81.30, "punta oeste, más allá de la ruptura (Soná–Golfo de Montijo–Coiba)"),
    ("W_third", -81.30, -81.00, "tercio oeste (Mariato–Río Grande)"),
    ("center", -81.00, -80.30, "centro (La Pitaloza–Tonosí–La Tronosa–Macaracas)"),
    ("E_third", -80.30, -80.00, "tercio este (Bajo Corral–Los Asientos–Pedasí)"),
    ("E_beyond", -80.00, 999, "punta este, mar adentro (SE de Pedasí, Golfo de Panamá)"),
]

def sector_of(lon: float) -> str:
    for key, lo, hi, _ in SECTORS:
        if lo <= lon < hi:
            return key
    return "?"

# ---------------------------------------------------------------------------
# 1. Descarga USGS
# ---------------------------------------------------------------------------
def fetch_catalog(starttime: str, endtime: str | None = None, minmagnitude: float = 2.5,
                  bbox: dict | None = None, orderby: str = "time-asc", timeout: int = 60) -> dict:
    """Devuelve el GeoJSON del FDSN event service. Fechas ISO (UTC)."""
    params = dict(format="geojson", starttime=starttime, minmagnitude=minmagnitude, orderby=orderby)
    if endtime: params["endtime"] = endtime
    params.update(bbox or PANAMA_BBOX)
    url = USGS_FDSN + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def fetch_event(event_id: str, timeout: int = 60) -> dict:
    """GeoJSON de un evento con todos sus productos (origin, moment-tensor, finite-fault, shakemap, losspager...)."""
    url = USGS_FDSN + "?" + urllib.parse.urlencode(dict(eventid=event_id, format="geojson"))
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def events_from_geojson(gj: dict) -> list[dict]:
    """Lista plana ordenada por tiempo: t (ms UTC), mag, lat, lon, depth, place, id."""
    out = []
    for f in gj["features"]:
        p, c = f["properties"], f["geometry"]["coordinates"]
        if p["mag"] is None: continue
        out.append(dict(id=f["id"], t=p["time"], mag=float(p["mag"]), mag_type=p.get("magType"),
                        lat=c[1], lon=c[0], depth=c[2], place=p.get("place"), status=p.get("status")))
    return sorted(out, key=lambda e: e["t"])

def local_str(t_ms: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return dt.datetime.fromtimestamp(t_ms / 1000, PANAMA_TZ).strftime(fmt)

# ---------------------------------------------------------------------------
# 2. Modelo Reasenberg–Jones (1989)
#    λ(t, M) = 10^(a + b(Mm − M)) · (t + c)^(−p)      [t en días]
#    N(M≥Mc, t1→t2) = 10^(a + b(Mm − Mc)) · [(t2+c)^(1−p) − (t1+c)^(1−p)] / (1−p)
#    P(≥1 evento) = 1 − exp(−N)
#    Parámetros genéricos (California): a=−1.67, b=0.91, p=1.08, c=0.05 d.
#    Ajustado a Panamá con las réplicas M≥5 observadas: a ≈ −1.77 (7 h), −1.87 (23 h).
# ---------------------------------------------------------------------------
RJ_GENERIC = dict(a=-1.67, b=0.91, p=1.08, c=0.05)

def rj_expected(Mm: float, Mc: float, t1_days: float, t2_days: float, a=-1.67, b=0.91, p=1.08, c=0.05) -> float:
    if t2_days <= t1_days: return 0.0
    K = 10 ** (a + b * (Mm - Mc))
    return K * (((t2_days + c) ** (1 - p) - (t1_days + c) ** (1 - p)) / (1 - p))

def rj_prob(Mm, Mc, t1_days, t2_days, **params) -> float:
    return 1 - math.exp(-rj_expected(Mm, Mc, t1_days, t2_days, **params))

def rj_fit_a(n_obs: int, Mm: float, Mc: float, t1_days: float, t2_days: float, b=0.91, p=1.08, c=0.05) -> float:
    """Ajusta 'a' para que el conteo esperado iguale al observado (b, p, c fijos)."""
    I = ((t2_days + c) ** (1 - p) - (t1_days + c) ** (1 - p)) / (1 - p)
    if n_obs <= 0 or I <= 0: return float("nan")
    return math.log10(n_obs / I) - b * (Mm - Mc)

def omori_rate_per_hour(Mm: float, Mc: float, t_days: float, a=-1.67, b=0.91, p=1.08, c=0.05) -> float:
    return 10 ** (a + b * (Mm - Mc)) * (t_days + c) ** (-p) / 24.0

def bath_expected_max(Mm: float, delta: float = 1.2) -> float:
    """Ley de Båth: réplica mayor ≈ Mm − 1.2 (dispersión típica ±0.4–0.5)."""
    return Mm - delta

# ---------------------------------------------------------------------------
# 3. Métricas de una secuencia
# ---------------------------------------------------------------------------
def count(events, t0_ms, mc, h1, h2):
    """Eventos con mag>=mc y h1 < horas <= h2 (horas desde t0)."""
    return sum(1 for e in events if e["mag"] >= mc and h1 < (e["t"] - t0_ms) / 3.6e6 <= h2)

def sequence_metrics(events: list[dict], Mm: float, t0_ms: int, now_ms: int, mc: float = 5.0,
                     bins_h=((0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 24), (24, 36), (36, 48), (48, 72), (72, 168)),
                     params=RJ_GENERIC) -> dict:
    """Resumen de la secuencia hasta now_ms: conteos por ventana obs/esp, razón, a ajustado,
    réplica máxima, escalada (máx. 2ª mitad > máx. 1ª mitad), sectores."""
    aft = [e for e in events if e["t"] > t0_ms and e["t"] <= now_ms]
    now_h = (now_ms - t0_ms) / 3.6e6
    rows = []
    for h1, h2 in bins_h:
        if h1 >= now_h: break
        h2 = min(h2, now_h)
        rows.append(dict(h1=h1, h2=round(h2, 2),
                         obs_45=count(aft, t0_ms, 4.5, h1, h2), esp_45=round(rj_expected(Mm, 4.5, h1/24, h2/24, **params), 2),
                         obs_5=count(aft, t0_ms, mc, h1, h2), esp_5=round(rj_expected(Mm, mc, h1/24, h2/24, **params), 2)))
    n5 = count(aft, t0_ms, mc, 0, now_h); e5 = rj_expected(Mm, mc, 0, now_h/24, **params)
    n6 = count(aft, t0_ms, 6.0, 0, now_h); e6 = rj_expected(Mm, 6.0, 0, now_h/24, **params)
    mags = [e["mag"] for e in aft]
    largest = max(aft, key=lambda e: e["mag"]) if aft else None
    half = now_h / 2
    m1 = max([e["mag"] for e in aft if (e["t"]-t0_ms)/3.6e6 <= half], default=float("nan"))
    m2 = max([e["mag"] for e in aft if (e["t"]-t0_ms)/3.6e6 > half], default=float("nan"))
    after_largest = [e for e in aft if largest and e["t"] > largest["t"]]
    sectors = {}
    for e in aft:
        sectors.setdefault(sector_of(e["lon"]), 0); sectors[sector_of(e["lon"])] += 1
    last6h = [e for e in aft if (now_ms - e["t"]) / 3.6e6 <= 6]
    return dict(
        now_h=round(now_h, 2), n_aftershocks=len(aft),
        n_ge5=n5, exp_ge5=round(e5, 1), ratio_ge5=round(n5/e5, 2) if e5 else None,
        n_ge6=n6, exp_ge6=round(e6, 2),
        a_fitted=round(rj_fit_a(n5, Mm, mc, 0, now_h/24), 2),
        largest=dict(mag=largest["mag"], h=round((largest["t"]-t0_ms)/3.6e6, 2), place=largest["place"], diff=round(largest["mag"]-Mm, 1)) if largest else None,
        bath_expected=bath_expected_max(Mm),
        max_after_largest=max([e["mag"] for e in after_largest], default=None),
        escalation=dict(max_first_half=m1, max_second_half=m2, escalating=(m2 > m1) if mags else None),
        hours_since_last_ge5=round((now_ms - max([e["t"] for e in aft if e["mag"] >= mc], default=t0_ms)) / 3.6e6, 2),
        sectors=sectors, sectors_last6h={k: sum(1 for e in last6h if sector_of(e["lon"]) == k) for k in sectors},
        bins=rows,
    )

def forecast_table(Mm: float, now_days: float, params=RJ_GENERIC, a_fit: float | None = None) -> list[dict]:
    """Pronóstico desde ahora: 24 h, 7 d, 30 d, 365 d — con parámetros genéricos y, si se da, con a ajustado."""
    out = []
    for label, dd in (("24 h", 1), ("7 días", 7), ("30 días", 30), ("1 año", 365)):
        row = dict(window=label)
        for tag, prm in (("gen", params), ("fit", dict(params, a=a_fit) if a_fit is not None else None)):
            if prm is None: continue
            row[tag] = dict(
                n_ge45=round(rj_expected(Mm, 4.5, now_days, now_days+dd, **prm), 1),
                n_ge5=round(rj_expected(Mm, 5.0, now_days, now_days+dd, **prm), 1),
                p_ge6=round(rj_prob(Mm, 6.0, now_days, now_days+dd, **prm), 3),
                p_ge7=round(rj_prob(Mm, 7.0, now_days, now_days+dd, **prm), 3),
                p_ge_main=round(rj_prob(Mm, Mm, now_days, now_days+dd, **prm), 4),
            )
        out.append(row)
    return out

# ---------------------------------------------------------------------------
# Señales que cambiarían la lectura ("semáforo"). Devuelve lista de alertas (strings).
# ---------------------------------------------------------------------------
def red_flags(metrics: dict, Mm: float) -> list[str]:
    flags = []
    if metrics["ratio_ge5"] and metrics["ratio_ge5"] >= 1.5:
        flags.append(f"Productividad {metrics['ratio_ge5']}× el promedio (≥1.5×): secuencia hiperproductiva, como Tohoku 2011, Ridgecrest 2019, Kamchatka 2025.")
    esc = metrics["escalation"]
    if esc["escalating"]:
        flags.append(f"Escalada: máximo de la 2ª mitad ({esc['max_second_half']}) mayor que el de la 1ª ({esc['max_first_half']}), como Tohoku, Ridgecrest, Santa Cruz 1980, Salomón 1971.")
    if metrics["largest"] and metrics["largest"]["diff"] > -0.8:
        flags.append(f"Réplica mayor a solo {abs(metrics['largest']['diff'])} grados del principal (umbral −0.8): demasiado grande para réplica.")
    tips = metrics["sectors_last6h"].get("W_beyond", 0) + metrics["sectors_last6h"].get("E_beyond", 0)
    if tips >= 3:
        flags.append(f"{tips} eventos en las puntas fuera de la ruptura en las últimas 6 h: vigilar migración hacia segmentos sin romper.")
    return flags
