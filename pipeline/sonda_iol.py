"""Sonda descartable: endpoints de la API de IOL (no imprime credenciales)."""
import os, sys, json, urllib.parse
sys.path.insert(0, os.path.dirname(__file__))
from titulos import IOLApi
import urllib.request
a = IOLApi(os.environ['IOL_USER'], os.environ['IOL_PASS'])
a.get('/api/v2/bCBA/Titulos/AL30/Cotizacion')
def raw(path):
    # status crudo, sin el filtro de get()
    r = urllib.request.Request('https://api.invertironline.com' + path, headers={'Authorization': 'Bearer ' + a.tok})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, x.read().decode()[:350]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:350]
j = a.get('/api/v2/cotizaciones-orleans/obligacionesNegociables/argentina/Todos')
print('ORLEANS_ON_KEYS', list(j['titulos'][0].keys()))
print('ORLEANS_ON_EJ', json.dumps([t for t in j['titulos'] if t['simbolo'] in ('TTCDO', 'YM34O', 'TTCDD')], ensure_ascii=False)[:1200])
j = a.get('/api/v2/cotizaciones-orleans/titulosPublicos/argentina/Todos')
print('ORLEANS_TP_EJ', json.dumps([t for t in j['titulos'] if t['simbolo'] in ('AO27D', 'T31Y7', 'AL30')], ensure_ascii=False)[:900])
j = a.get('/api/v2/cotizaciones-orleans/letras/argentina/Todos')
print('ORLEANS_LE_EJ', json.dumps([t for t in j['titulos'] if t['simbolo'].startswith('S')][:2], ensure_ascii=False)[:600])
for s, d0, d1, aj in [('T31Y7', '2026-11-01', '2026-11-06', 'sinAjustar'), ('T31Y7', '2026-10-01', '2026-11-25', 'sinAjustar'),
                      ('T31Y7', '2026-10-01', '2026-11-25', 'ajustada'), ('TTCDO', '2026-10-01', '2026-11-25', 'sinAjustar'),
                      ('AO27D', '2026-10-01', '2026-11-25', 'sinAjustar'), ('TTCDD', '2026-10-01', '2026-11-25', 'sinAjustar'),
                      ('S30E7', '2026-10-01', '2026-11-25', 'sinAjustar'), ('YM34O', '2026-10-01', '2026-11-25', 'sinAjustar'),
                      ('AL30', '2026-10-01', '2026-11-25', 'sinAjustar')]:
    st, body = raw(f'/api/v2/bCBA/Titulos/{s}/Cotizacion/seriehistorica/{d0}/{d1}/{aj}')
    print('SERIE', s, d0, d1, aj, st, len(body), body[:200].replace('\n', ' '))
