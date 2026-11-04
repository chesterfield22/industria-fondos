"""IOL dentro de Adcap: serie diaria de AUM y participación, y posición en el ranking de gerentes si IOL fuera
una sociedad gerente separada.

IOL = fondos de Adcap cuyo nombre empieza con 'IOL ' (metrics.es_iol). AUM diario por clase: último dato con
antigüedad <= 5 ruedas (mismo criterio que aum_diario). Clases USD a pesos al A3500 de cada día.

Columnas (millones):
  iol_ars_m, adcap_ars_m, share_ars       clases en pesos (ARS M)
  iol_usd_m, adcap_usd_m, share_usd       clases en dólares (USD M)
  iol_tot_ars_m, adcap_tot_ars_m, share_tot   todo, en pesos (USD al A3500)
  iol_tot_usd_m, adcap_tot_usd_m          todo, en dólares (ARS / A3500); share idéntico a share_tot
  share_iol_industria                     IOL / industria (total en pesos)
  pos_iol, pos_adcap_ex_iol               posición por AUM total si IOL fuera gerente separado
  pos_adcap                               posición real de Adcap (con IOL)
  n_gerentes                              gerentes con AUM > 0 ese día (con IOL separado)
"""
import os
import numpy as np, pandas as pd, duckdb
from metrics import es_iol, split_iol


def serie(base_dir, T, desde='2022-01-01'):
    T = pd.Timestamp(T)
    m = pd.read_parquet(os.path.join(base_dir, 'maestro.parquet'))
    m = m[~m.subtipo.isin(['Fuera de universo'])].drop_duplicates('clase_id').set_index('clase_id')
    d = duckdb.sql(f"""select fecha, clase_id, aum from read_parquet('{base_dir}/clases_diario_*.parquet')
                       where aum > 0 and fecha >= '{desde}' and fecha <= '{T.date()}'""").df()
    d = d[d.clase_id.isin(m.index)]
    d['fecha'] = pd.to_datetime(d.fecha)
    bd = pd.bdate_range(desde, T)
    P = d.pivot_table(index='fecha', columns='clase_id', values='aum').reindex(bd).ffill(limit=5)
    fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), index_col=0, parse_dates=True).a3500.reindex(bd).ffill()
    mm = m.reindex(P.columns)
    usd = mm.moneda.eq('USD').values
    iol = es_iol(mm).values
    adc = mm.gerente.eq('Adcap').values
    Pa = P.copy()
    Pa.loc[:, usd] = P.loc[:, usd].mul(fx, axis=0)
    s = lambda X, mask: X.loc[:, mask].sum(axis=1, min_count=1) / 1e6
    o = pd.DataFrame(index=bd)
    o['iol_ars_m'], o['adcap_ars_m'] = s(P, iol & ~usd), s(P, adc & ~usd)
    o['iol_usd_m'], o['adcap_usd_m'] = s(P, iol & usd), s(P, adc & usd)
    o['iol_tot_ars_m'], o['adcap_tot_ars_m'] = s(Pa, iol), s(Pa, adc)
    o = o.fillna({c: 0.0 for c in o.columns if c.startswith('iol_')})
    o['share_ars'] = o.iol_ars_m / o.adcap_ars_m
    o['share_usd'] = o.iol_usd_m / o.adcap_usd_m
    o['share_tot'] = o.iol_tot_ars_m / o.adcap_tot_ars_m
    o['iol_tot_usd_m'] = o.iol_tot_ars_m / fx
    o['adcap_tot_usd_m'] = o.adcap_tot_ars_m / fx
    o['share_iol_industria'] = o.iol_tot_ars_m / (Pa.sum(axis=1) / 1e6)
    # rankings de gerentes por AUM total (en pesos)
    Gs = Pa.T.groupby(split_iol(mm).values).sum(min_count=1).T
    Rs = Gs.where(Gs > 0).rank(axis=1, ascending=False, method='min')
    Gn = Pa.T.groupby(mm.gerente.values).sum(min_count=1).T
    Rn = Gn.where(Gn > 0).rank(axis=1, ascending=False, method='min')
    o['pos_iol'] = Rs.get('IOL')
    o['pos_adcap_ex_iol'] = Rs.get('Adcap ex IOL')
    o['pos_adcap'] = Rn.get('Adcap')
    o['n_gerentes'] = (Gs > 0).sum(axis=1)
    o = o[o.adcap_tot_ars_m.notna()]
    o.loc[o.iol_tot_ars_m <= 0, ['pos_iol']] = np.nan
    o.index.name = 'fecha'
    mens = o.groupby(o.index.to_period('M')).tail(1)
    return o, mens


def publish(base_dir, out, T):
    o, mens = serie(base_dir, T)
    for df, f in ((o, 'iol_adcap_diario.csv'), (mens, 'iol_adcap_mensual.csv')):
        x = df.reset_index()
        for c in x.columns:
            if c.startswith('pos_') or c == 'n_gerentes':
                x[c] = x[c].astype('Int64')
        x.round({c: (6 if c.startswith('share') else 2) for c in x.columns if c != 'fecha'}) \
         .to_csv(os.path.join(out, f), index=False, date_format='%Y-%m-%d')
    return o
