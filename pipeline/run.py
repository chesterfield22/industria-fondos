"""Corrida diaria completa: tipos de cambio -> base -> maestro -> métricas -> CSV publicados.
Uso: python pipeline/run.py <ruta ArgentinaDatos datos/v1> <dir base> <dir salida> [fecha_corte]"""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import fx, base, maestro, publish, metrics

ad, base_dir, out = sys.argv[1], sys.argv[2], sys.argv[3]
T = sys.argv[4] if len(sys.argv) > 4 else None
os.makedirs(base_dir, exist_ok=True); os.makedirs(out, exist_ok=True)
ref = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'ref')
fx.build(os.path.join(ad, 'cotizaciones', 'dolares'), os.path.join(base_dir, 'fx.csv'))
base.build(os.path.join(ad, 'finanzas', 'fci', 'fondos'), base_dir, os.path.join(base_dir, 'fx.csv'))
completa = str(metrics.fecha_corte(base_dir)[1].date())
m, mt = maestro.build(base_dir, ref, T or completa)
m.to_parquet(os.path.join(base_dir, 'maestro.parquet'), index=False)
mt.to_csv(os.path.join(out if os.path.isdir(out) else base_dir, 'match1816_log.csv'), index=False)
publish.publish(base_dir, out, T)
