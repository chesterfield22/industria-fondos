"""Corrida diaria completa: tipos de cambio -> base (CNV + historia) -> maestro -> métricas -> CSV publicados.
Uso: python pipeline/run.py <ruta ArgentinaDatos datos/v1> <dir base> <dir salida> [fecha_corte]
Variables opcionales: PREV_BASE (dir con la base publicada anterior, respaldo si ArgentinaDatos no está), CNV_DIAS (default 10)."""
import os, sys, json, shutil
sys.path.insert(0, os.path.dirname(__file__))
import fx, base, maestro, publish, metrics, cnv

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
completa = str(metrics.fecha_corte(base_dir)[1].date())
m, mt = maestro.build(base_dir, ref, T or completa)
m.to_parquet(os.path.join(base_dir, 'maestro.parquet'), index=False)
mt.to_csv(os.path.join(out if os.path.isdir(out) else base_dir, 'match1816_log.csv'), index=False)
publish.publish(base_dir, out, T)
