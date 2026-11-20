"""Corrida diaria completa: tipos de cambio -> base (CNV + historia) -> maestro -> métricas -> CSV publicados.
Uso: python pipeline/run.py <ruta ArgentinaDatos datos/v1> <dir base> <dir salida> [fecha_corte]
Variables opcionales: PREV_BASE (dir con la base publicada anterior, respaldo si ArgentinaDatos no está), CNV_DIAS (default 10)."""
import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(__file__))
import fx, base, maestro, publish, metrics, cnv
import pandas as pd

ad, base_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
T = (sys.argv[4] if len(sys.argv) > 4 else '') or None
prev = os.environ.get('PREV_BASE') or None
os.makedirs(base_dir, exist_ok=True); os.makedirs(out, exist_ok=True)
ref = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ref')
dol = os.path.join(ad, 'cotizaciones', 'dolares')
fx_csv = os.path.join(base_dir, 'fx.csv')
if os.path.isdir(os.path.join(dol, 'bolsa')):
    fx.build(dol, fx_csv)
elif prev and os.path.exists(os.path.join(prev, 'fx.csv')):
    shutil.copy(os.path.join(prev, 'fx.csv'), fx_csv)       # sin ArgentinaDatos: tipos de cambio de la corrida anterior
    open(os.path.join(base_dir, 'fx_fuente.txt'), 'w').write('corrida anterior (ArgentinaDatos no disponible)')
else:
    raise RuntimeError('Sin tipos de cambio: ArgentinaDatos no disponible y no hay corrida anterior')
filas, meta_cnv, info = cnv.descargar(dias=int(os.environ.get('CNV_DIAS', 10)))
json.dump(info, open(os.path.join(base_dir, 'cnv_info.json'), 'w'))
base.build(os.path.join(ad, 'finanzas', 'fci', 'fondos'), base_dir, fx_csv, filas, meta_cnv, prev)
Tc = T or str(metrics.corte(base_dir)[0].date())        # misma fecha que usará publish
m, mt = maestro.build(base_dir, ref, Tc)
m.to_parquet(os.path.join(base_dir, 'maestro.parquet'), index=False)
mt.to_csv(os.path.join(out if os.path.isdir(out) else base_dir, 'match1816_log.csv'), index=False)
publish.publish(base_dir, out, T)

# no reemplazar datos publicados por otros peores (p. ej. la CNV no respondió y la corrida anterior sí la tenía):
# se publica sólo si la fecha de corte es posterior, o igual sin pasar de 'definitivo' a 'preliminar'
prev_meta = os.environ.get('PREV_META')
if not T and prev_meta and os.path.exists(prev_meta):
    try:
        old = dict(pd.read_csv(prev_meta).values); new = dict(pd.read_csv(os.path.join(out, 'meta.csv')).values)
        rk = lambda m: (str(m.get('fecha_corte')), 1 if m.get('estado_corte', 'definitivo') == 'definitivo' else 0)
        if rk(new) < rk(old):
            msg = f"corte nuevo {rk(new)} peor que el publicado {rk(old)} (CNV: {info.get('error') or 'ok'})"
            open(os.path.join(out, 'NO_PUBLICAR'), 'w').write(msg)
            print('NO SE PUBLICA:', msg)
    except Exception as e:
        print('control de publicación omitido:', e)
