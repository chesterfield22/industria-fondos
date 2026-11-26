"""Sonda descartable: estructura de paneles de la API de IOL (no imprime credenciales)."""
import os, sys, json, urllib.parse
sys.path.insert(0, os.path.dirname(__file__))
from titulos import IOLApi
a = IOLApi(os.environ['IOL_USER'], os.environ['IOL_PASS'])
q = urllib.parse.quote
for inst in ['Bonos', 'bonos', 'titulosPublicos', 'obligacionesNegociables', 'letras', 'cuponesPBI', 'Obligaciones Negociables']:
    p = a.get(f'/api/v2/argentina/Titulos/Cotizacion/Paneles/{q(inst)}')
    print('PANELES', inst, json.dumps(p, ensure_ascii=False)[:600])
for inst, pan in [('bonos', 'todos'), ('Bonos', 'Todos'), ('titulosPublicos', 'todos'), ('obligacionesNegociables', 'todos'),
                  ('bonos', 'obligacionesNegociables'), ('bonos', 'titulosPublicos'), ('bonos', 'Obligaciones Negociables'),
                  ('bonos', 'Títulos Públicos'), ('bonos', 'Letras'), ('Bonos', 'Corporativos'), ('Bonos', 'Soberanos en dólares')]:
    j = a.get(f'/api/v2/Cotizaciones/{q(inst)}/{q(pan)}/argentina')
    t = (j or {}).get('titulos', []) if isinstance(j, dict) else []
    print('COTIZ', inst, '|', pan, '|', len(t), [x.get('simbolo') for x in t[:8]], json.dumps(t[0], ensure_ascii=False)[:300] if t else '')
for s in ['TTCDO', 'YM34O', 'AO27D', 'T31Y7']:
    j = a.get(f'/api/v2/bCBA/Titulos/{s}')
    print('TITULO', s, json.dumps(j, ensure_ascii=False)[:400])
j = a.get('/api/v2/bCBA/Titulos/T31Y7/Cotizacion/seriehistorica/2026-11-01/2026-11-06/sinAjustar')
print('SERIE', json.dumps(j, ensure_ascii=False)[:700])
