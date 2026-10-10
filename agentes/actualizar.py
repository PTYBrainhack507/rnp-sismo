#!/usr/bin/env python3
"""
actualizar.py — el agente de ingesta. Corre en la EliteBook cada 15 minutos (systemd timer).

1. Descarga el catálogo USGS de la secuencia.
2. Construye datos/estado.json (motor/estado.py) y guarda copia en datos/historial/.
3. Si el estado cambió, hace commit y push: la página lo lee desde GitHub.
4. Si el USGS no responde, no toca nada: la página sigue mostrando el último estado válido
   y lo marca como viejo por la hora de corte.

Todo queda en bitácora en logs/actualizar.log. Sin API keys, sin modelos: solo datos públicos.
"""
import json, os, sys, subprocess, datetime as dt, traceback, urllib.error

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RAIZ, "motor"))
LOG = os.path.join(RAIZ, "logs", "actualizar.log")
ESTADO = os.path.join(RAIZ, "datos", "estado.json")
HIST = os.path.join(RAIZ, "datos", "historial")


def log(msg):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    linea = f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(linea)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def git(*args, check=True):
    return subprocess.run(["git", "-C", RAIZ, *args], capture_output=True, text=True, check=check)


def main():
    from estado import construir, descargar
    publicar = "--sin-git" not in sys.argv
    try:
        gj = descargar()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        log(f"USGS no respondió: {e}. Se conserva el estado anterior.")
        return 0
    est = construir(gj)
    nuevo = json.dumps(est, ensure_ascii=False, separators=(",", ":"))
    viejo = open(ESTADO, encoding="utf-8").read() if os.path.exists(ESTADO) else ""
    # Comparar sin la hora de generación: si solo cambió el reloj, igual publicamos cada hora
    def sin_reloj(s):
        try:
            d = json.loads(s); d.pop("generado_utc", None); d.pop("generado_ms", None); d.pop("generado_local", None)
            d.pop("boletin", None); d["metricas"].pop("now_h", None); d.pop("ahora_h", None); d.pop("carrera", None); d.pop("pronostico", None); d.pop("comparacion", None)
            return json.dumps(d, sort_keys=True)
        except Exception:
            return s
    cambio_datos = sin_reloj(nuevo) != sin_reloj(viejo)
    minuto = dt.datetime.now(dt.timezone.utc).minute
    publicar_ahora = cambio_datos or minuto < 15  # cada hora, aunque no haya réplicas nuevas, para refrescar reloj y carrera
    os.makedirs(HIST, exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as f:
        f.write(nuevo)
    sello = dt.datetime.fromtimestamp(est["generado_ms"] / 1000, dt.timezone.utc).strftime("%Y-%m-%d_%H%M")
    if cambio_datos:
        with open(os.path.join(HIST, f"{sello}.json"), "w", encoding="utf-8") as f:
            f.write(nuevo)
    m = est["metricas"]
    log(f"{est['generado_local']} | {m['now_h']} h | réplicas {m['n_aftershocks']} | M≥5 {m['n_ge5']}/{m['exp_ge5']} ({m['ratio_ge5']}×) | "
        f"semáforo {est['semaforo']['estado']} | carrera {est['carrera']['superadas']}/{est['carrera']['total']} | cambio={cambio_datos}")
    if publicar and publicar_ahora:
        git("add", "datos/estado.json", "datos/historial")
        r = git("commit", "-q", "-m", f"estado {sello} · {m['n_aftershocks']} réplicas · {est['semaforo']['estado']}", check=False)
        if r.returncode == 0:
            p = git("push", "-q", check=False)
            log("push ok" if p.returncode == 0 else f"push falló: {p.stderr.strip()[:300]}")
        else:
            log("sin cambios que publicar")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("ERROR " + traceback.format_exc().replace("\n", " | ")[:1500])
        sys.exit(1)
