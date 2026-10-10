# rnp-sismo — seguimiento de réplicas del terremoto de Azuero (Mw 7,7, 9-oct-2026)

Servicio informativo a la comunidad de Recicladora Nacional de Panamá, publicado en
`recicladoranacional.com/serviciocomunitario`.

- `motor/` — modelo estadístico (Reasenberg–Jones, Omori, Båth), comparación con 9 secuencias
  históricas "dobles", señales y semáforo. Solo librería estándar de Python.
- `agentes/actualizar.py` — agente de ingesta: descarga el catálogo USGS cada 15 min, escribe
  `datos/estado.json`, guarda historial y publica con git push. `instalar_elitebook.sh` lo deja
  corriendo como timer de systemd.
- `datos/estado.json` — lo que lee la página. `datos/mapa.json` — fondo del mapa (Natural Earth +
  modelo de falla finita USGS), estático.

La página (`rnp_web/www/serviciocomunitario.html`) lee `estado.json` desde este repo (raw de GitHub)
y, si no está disponible, desde la copia que viaja con el deploy. Sin API keys, sin modelos de lenguaje:
todos los textos son plantillas fijas rellenadas con números del USGS.
