"""Publica los CSV que lee Google Sheets (IMPORTDATA con locale en_US: punto decimal, coma separador).

salida/
  meta.csv            fecha de corte, cobertura, tipos de cambio
  segmentos.csv       AUM, suscripciones y rendimientos por tipo/subtipo (esquema 1816)
  gerentes.csv        AUM, suscripciones, share por sociedad gerente (+ tipo bancaria/independiente)
  gerentes_tipo.csv   AUM por gerente x tipo de FCI;  gerentes_subtipo.csv  AUM por gerente x subtipo
  gerentes_segmento.csv  gerente x segmento (Total / tipo / subtipo): AUM, share en el segmento, suscripciones
  gerentes_iol.csv    idem con IOL separado de Adcap ('IOL' y 'Adcap ex IOL')
  iol_adcap_diario.csv / iol_adcap_mensual.csv   participación de IOL en Adcap y posición en el ranking
  tipo_gerente.csv    bancarias vs independientes
  ranking.csv         clases (fondos >= ARS 1.000 M): rendimientos por horizonte y posición en su subtipo, vol, honorarios
  clases.csv          catálogo de clases activas (para el selector de fondo)
  aum_diario.csv      AUM diario por tipo (ARS M), último año
  flujo_diario.csv    suscripciones netas diarias por tipo (ARS M), último año
  fx.csv              A3500, MEP, CCL diarios
  serie/<clase_id>.csv  historia por clase: fecha, vcp, aum, suscripciones_netas, rend_diario (moneda de la clase), aum_ars, susc_ars (A3500 diario)
"""
import os, sys, json, glob, shutil
import numpy as np, pandas as pd, duckdb
sys.path.insert(0, os.path.dirname(__file__))
from metrics import compute, HZ
import iol


def w(df, path, nd=6):
    df.to_csv(path, index=False, float_format=f'%.{nd}f', date_format='%Y-%m-%d')


def publish(base_dir, out, T=None):
    os.makedirs(out, exist_ok=True)
    r = compute(base_dir, out, T)
    meta = r['meta']
    T = pd.Timestamp(meta['fecha_corte'])
    mrows = [('fecha_corte', meta['fecha_corte']), ('ultima_fecha_datos', meta['ultima_fecha_datos']),
             ('ultima_fecha_completa', meta['ultima_fecha_completa']), ('clases_con_dato', meta['clases_con_dato_T']),
             ('cobertura_aum', round(meta['cobertura_aum_T'], 6)),
             ('aum_arrastrado_pct', round(meta['aum_stale_pct'], 6)), ('fx_usado', 'A3500'),
             ('a3500', meta['fx_T']['a3500']), ('mep', meta['fx_T']['mep']), ('ccl', meta['fx_T']['ccl']),
             ('generado_utc', pd.Timestamp.now(tz='UTC').strftime('%Y-%m-%d %H:%M')),
             ('fuente_fondos', 'CNV planilla diaria de cuotapartes (via ArgentinaDatos)'),
             ('fuente_a3500', open(os.path.join(base_dir, 'fx_fuente.txt')).read() if os.path.exists(os.path.join(base_dir, 'fx_fuente.txt')) else 's/d'),
             ('fuente_mep_ccl', 'ArgentinaDatos / dolarapi (venta)')]
    pd.DataFrame(mrows, columns=['clave', 'valor']).to_csv(os.path.join(out, 'meta.csv'), index=False)
    S = r['seg'].copy()
    cols = ['tipo', 'subtipo', 'aum_ars_m', 'aum_usd_m', 'aum_total_m', 'pct_total'] + [f'susc_{h}_m' for h in HZ] + \
           ['susc_YTD_pct_aum', 'susc_365d_pct_aum'] + [f'rend_{h}' for h in HZ] + ['n_fondos', 'n_clases', 'aum_stale_pct']
    w(S[cols], os.path.join(out, 'segmentos.csv'))
    G = r['ger'].copy()
    w(G[['gerente', 'tipo_gerente', 'aum_total_m', 'share', 'aum_ars_m', 'aum_usd_m'] + [f'susc_{h}_m' for h in HZ] + ['n_fondos']],
      os.path.join(out, 'gerentes.csv'))
    w(r['ger_tipo'].reset_index(), os.path.join(out, 'gerentes_tipo.csv'))
    w(r['ger_sub'].reset_index(), os.path.join(out, 'gerentes_subtipo.csv'))
    w(r['ger_seg'], os.path.join(out, 'gerentes_segmento.csv'))
    w(r['ger_iol'], os.path.join(out, 'gerentes_iol.csv'))
    iol.publish(base_dir, out, T)
    w(r['tipo_ger'][['tipo_gerente', 'aum_total_m'] + [f'susc_{h}_m' for h in HZ]], os.path.join(out, 'tipo_gerente.csv'))
    RK = r['ranking'].copy()
    RK['aum_clase_m'] = RK.aum_ars / 1e6; RK['aum_fondo_m'] = RK.aum_fondo_ars / 1e6
    rk_cols = ['clase_id', 'clase', 'fondo', 'gerente', 'tipo_gerente', 'tipo', 'subtipo', 'moneda', 'aum_clase_m', 'aum_fondo_m'] + \
              sum([[f'r_{h}', f'pos_{h}'] for h in HZ], []) + ['vol_90d', 'rend_vol', 'hon_gd', 'hon_gerente', 'hon_depositaria',
                                                              'fecha_dato', 'origen_segmento']
    RK = RK.sort_values(['subtipo', 'aum_clase_m'], ascending=[True, False])[rk_cols]
    rnd = {'aum_clase_m': 2, 'aum_fondo_m': 2, 'vol_90d': 6, 'rend_vol': 4, 'hon_gd': 4, 'hon_gerente': 4, 'hon_depositaria': 4}
    rnd.update({f'r_{h}': 8 for h in HZ})
    RK = RK.round(rnd)
    for h in HZ:
        RK[f'pos_{h}'] = RK[f'pos_{h}'].astype('Int64')
    RK.to_csv(os.path.join(out, 'ranking.csv'), index=False, date_format='%Y-%m-%d')
    sn = r['snap']
    cat = sn[['clase_id', 'clase', 'fondo', 'gerente', 'tipo_gerente', 'tipo', 'subtipo', 'moneda', 'aum', 'aum_ars', 'fecha_dato',
              'origen_segmento', 'hon_gd'] + [f'r_{h}' for h in HZ] + ['vol_90d', 'rend_vol']].copy()
    cat['aum_ars_m'] = cat.aum_ars / 1e6
    cat = cat.drop(columns=['aum_ars']).sort_values('aum_ars_m', ascending=False).round(
        {'aum': 2, 'aum_ars_m': 2, 'hon_gd': 4, 'vol_90d': 6, 'rend_vol': 4, **{f'r_{h}': 8 for h in HZ}})
    cat.to_csv(os.path.join(out, 'clases.csv'), index=False, date_format='%Y-%m-%d')
    for k in ('aum_diario', 'flujo_diario'):
        x = r[k].reset_index().rename(columns={'index': 'fecha'})
        w(x, os.path.join(out, f'{k}.csv'), 2)
    fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), parse_dates=['fecha'])
    w(fx[fx.fecha > T - pd.Timedelta(days=800)], os.path.join(out, 'fx.csv'), 4)
    # series por clase (clases activas en los últimos 2 años)
    sd = os.path.join(out, 'serie'); shutil.rmtree(sd, ignore_errors=True); os.makedirs(sd)
    con = duckdb.connect()
    act = con.sql(f"""select distinct clase_id from read_parquet('{base_dir}/clases_diario_*.parquet')
                      where fecha > cast('{T.date()}' as date) - interval 730 day and aum > 0""").df().clase_id
    con.register('act', pd.DataFrame({'clase_id': act}))
    allr = con.sql(f"""select d.clase_id, d.fecha, d.vcp, d.aum, d.flujo, d.rend from read_parquet('{base_dir}/clases_diario_*.parquet') d
                       join act using (clase_id) where d.fecha <= '{T.date()}' order by d.clase_id, d.fecha""").df()
    # aum_ars / susc_ars: clases USD a pesos al A3500 de cada día (mismo criterio que los agregados)
    mon = pd.read_parquet(os.path.join(base_dir, 'clases_meta.parquet')).drop_duplicates('clase_id').set_index('clase_id').moneda
    a35 = fx.set_index('fecha').a3500
    allr['fecha'] = pd.to_datetime(allr.fecha)
    k = np.where(allr.clase_id.map(mon).eq('USD'), allr.fecha.map(a35), 1.0)
    allr['aum_ars'] = allr.aum * k
    allr['susc_ars'] = allr.flujo * k
    for cid, g in allr.groupby('clase_id', sort=False):
        g = g.drop(columns='clase_id').rename(columns={'flujo': 'suscripciones_netas', 'rend': 'rend_diario'})
        g = g.round({'vcp': 6, 'aum': 2, 'suscripciones_netas': 2, 'rend_diario': 8, 'aum_ars': 2, 'susc_ars': 2})
        g.to_csv(os.path.join(sd, f'{cid}.csv'), index=False, date_format='%Y-%m-%d')
    print('publicado', out, '| fecha corte', T.date(), '| series:', len(act))
    return r


if __name__ == '__main__':
    publish(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
