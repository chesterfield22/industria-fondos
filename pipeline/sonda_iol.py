"""Sonda descartable: alcance del error 500 en series de IOL y respaldo BYMA (no imprime credenciales)."""
import os, sys, json, random, urllib.parse, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from titulos import IOLApi, UA
a = IOLApi(os.environ['IOL_USER'], os.environ['IOL_PASS'])
a.get('/api/v2/bCBA/Titulos/AL30/Cotizacion')
def raw(path):
    r = urllib.request.Request('https://api.invertironline.com' + path, headers={**UA, 'Authorization': 'Bearer ' + a.tok})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, x.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, ''
for d0, d1 in [('2026-11-03', '2026-11-04'), ('2026-11-04', '2026-11-05'), ('2026-11-05', '2026-11-06'), ('2026-11-02', '2026-11-03'), ('2026-10-20', '2026-10-31')]:
    st, b = raw(f'/api/v2/bCBA/Titulos/T31Y7/Cotizacion/seriehistorica/{d0}/{d1}/sinAjustar'); print('DIA', d0, d1, st, b[:160])
stats = {}
for inst in ['titulosPublicos', 'letras', 'obligacionesNegociables']:
    j = a.get(f'/api/v2/cotizaciones-orleans/{inst}/argentina/Todos')
    act = [t for t in j['titulos'] if str(t.get('fecha', ''))[:4] == '2026' and (t.get('cantidadOperaciones') or 0) > 0]
    random.seed(1); mu = random.sample(act, min(25, len(act)))
    for t in mu:
        st, b = raw(f"/api/v2/bCBA/Titulos/{t['simbolo']}/Cotizacion/seriehistorica/2026-10-27/2026-11-07/sinAjustar")
        stats.setdefault(inst, []).append((t['simbolo'], st, b.count('fechaHora')))
    print('MUESTRA', inst, len(act), 'activos', stats[inst])
def byma(url, body=None):
    r = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                               headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        return urllib.request.urlopen(r, timeout=30).read().decode()[:400]
    except Exception as ex:
        return f'EXC {ex}'
import time
t0, t1 = int(time.mktime((2026, 10, 27, 0, 0, 0, 0, 0, 0))), int(time.mktime((2026, 11, 7, 0, 0, 0, 0, 0, 0)))
for sym in ['T31Y7 24HS', 'T31Y7', 'TTCDO 24HS']:
    u = 'https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/chart/historical-series/history?' + urllib.parse.urlencode({'symbol': sym, 'resolution': 'D', 'from': t0, 'to': t1})
    print('BYMAHIST', sym, byma(u))
