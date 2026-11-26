"""Sonda descartable: endpoints de la API de IOL y BYMA (no imprime credenciales)."""
import os, sys, json, urllib.parse, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(__file__))
from titulos import IOLApi
a = IOLApi(os.environ['IOL_USER'], os.environ['IOL_PASS'])
q = urllib.parse.quote
def raw(path):
    tok = a._tok if hasattr(a, '_tok') else None
    try:
        return a.get(path)
    except Exception as ex:
        return f'EXC {type(ex).__name__} {ex}'
print('ATTRS', [k for k in vars(a)])
for p in ['/api/v2/bCBA/Titulos/T31Y7/Cotizacion/seriehistorica/2026-11-01/2026-11-06/sinAjustar',
          '/api/v2/bcba/Titulos/T31Y7/Cotizacion/seriehistorica/2026-11-01/2026-11-06/sinAjustar',
          '/api/v2/BCBA/Titulos/T31Y7/Cotizacion/seriehistorica/2026-11-01/2026-11-06/sinAjustar',
          '/api/v2/bCBA/Titulos/T31Y7/Cotizacion/seriehistorica/2026-11-01/2026-11-06/ajustada',
          '/api/v2/bCBA/Titulos/AL30/Cotizacion/seriehistorica/2026-11-01/2026-11-06/sinAjustar',
          '/api/v2/bCBA/Titulos/T31Y7/Cotizacion',
          '/api/v2/bCBA/Titulos/T31Y7/CotizacionDetalle']:
    j = raw(p); print('SERIE', p, '=>', json.dumps(j, ensure_ascii=False)[:400])
for p in ['/api/v2/cotizaciones-orleans/obligacionesNegociables/argentina/Todos',
          '/api/v2/cotizaciones-orleans/obligacionesNegociables/argentina/Operables',
          '/api/v2/cotizaciones-orleans/titulosPublicos/argentina/Todos',
          '/api/v2/cotizaciones-orleans/letras/argentina/Todos',
          '/api/v2/cotizaciones-orleans/Bonos/argentina/Todos',
          '/api/v2/Cotizaciones/obligacionesNegociables/argentina/Todos',
          '/api/v2/Cotizaciones/Bonos/Bonos%20corporativos%20en%20pesos/argentina',
          '/api/v2/Cotizaciones/Bonos/Bonos%20corporativos%20en%20d%C3%B3lares/argentina',
          '/api/v2/Cotizaciones/Bonos/Soberanos%20en%20pesos%20m%C3%A1s%20Cer/argentina']:
    j = raw(p)
    t = (j or {}).get('titulos', []) if isinstance(j, dict) else (j if isinstance(j, list) else [])
    print('LISTA', p, '=>', len(t) if isinstance(t, list) else t, [x.get('simbolo') for x in t[:6]] if isinstance(t, list) else '', json.dumps(t[0], ensure_ascii=False)[:250] if isinstance(t, list) and t else json.dumps(j)[:200])
# BYMA bulk
def byma(path, body):
    r = urllib.request.Request('https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/' + path,
        data=json.dumps(body).encode(), headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        return json.loads(urllib.request.urlopen(r, timeout=30).read())
    except Exception as ex:
        return f'EXC {ex}'
for path in ['negociable-obligations', 'public-bonds', 'lebacs', 'short-term-government-bonds']:
    j = byma(path, {"excludeZeroPxAndQty": False, "T2": True, "T1": False, "T0": False, "Content-Type": "application/json"})
    d = j.get('data', j) if isinstance(j, dict) else j
    print('BYMA', path, '=>', (len(d) if isinstance(d, list) else str(d)[:200]), json.dumps(d[0], ensure_ascii=False)[:500] if isinstance(d, list) and d else '')
