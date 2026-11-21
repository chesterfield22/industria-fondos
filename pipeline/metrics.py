"""Métricas de industria (réplica del esquema 1816 + extras).

Convenciones (verificadas contra el informe 1816 del 02/11/2026):
  - Rendimiento clase h: VCP_T / VCP_base - 1; base = último VCP <= T - h días corridos
    (MTD: último del mes anterior; YTD: último del año anterior).
  - Suscripciones netas h: suma de flujos diarios con fecha en (T-h, T], cada día convertido a ARS al tipo de cambio de ese día.
  - AUM a T: último dato de la clase con antigüedad <= 7 días (si no reportó T se arrastra y se marca 'stale').
  - Fondos USD a ARS: A3500 por defecto (también se publican totales a MEP y CCL).
  - Rendimiento de segmento: promedio ponderado por AUM (ARS) de las clases, neto de comisiones.
"""
import os, sys, json
import numpy as np, pandas as pd, duckdb

HZ = {'1d': 1, '7d': 7, 'MTD': 'MTD', '30d': 30, '90d': 90, 'YTD': 'YTD', '365d': 365}
ORDEN = ['MM ARS Clásico', 'MM ARS Dinámico', 'MM USD Clásico', 'T+1', 'RF ARS Lecap', 'RF ARS CER', 'RF ARS Disc', 'RF MULT Corp',
         'RF MULT Disc', 'RF USD Linked', 'RF USD CP', 'RF USD corp loc', 'RF USD Disc', 'RF USD GL Inc ARG', 'RF USD GL Ex ARG',
         'RV ARG Merval', 'RV ARG Rofex 20', 'RV ARG Otros', 'RV Global', 'RM y Otros ARS', 'RM y Otros USD', 'PYME', 'INFRA', 'ASG']
TIPOS = ['Money Market', 'T+1', 'Renta Fija', 'Renta Variable', 'Renta Mixta', 'Destino Esp.']


def base_date(T, h):
    if h == 'MTD':
        return T.replace(day=1) - pd.Timedelta(days=1)
    if h == 'YTD':
        return pd.Timestamp(T.year - 1, 12, 31)
    return T - pd.Timedelta(days=h)


def load(base_dir):
    con = duckdb.connect()
    d = con.sql(f"select fecha, clase_id, vcp, aum, flujo, rend, flag from read_parquet('{base_dir}/clases_diario_*.parquet') where fecha >= '2024-10-01'").df()
    m = pd.read_parquet(os.path.join(base_dir, 'maestro.parquet'))
    fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), index_col=0, parse_dates=True)
    return d, m, fx


MIN_N, MIN_AUM = 0.95, 0.985          # corte definitivo
PRE_N, PRE_AUM = 0.85, 0.80           # corte preliminar (como 1816: el resto de las clases arrastra su último dato)
MODO_CORTE = os.environ.get('MODO_CORTE', 'preliminar')   # 'preliminar' (más reciente) o 'definitivo'


def fecha_corte(base_dir, min_n=MIN_N, min_aum=MIN_AUM, dias=70):
    """Última fecha hábil 'completa': clases reportadas >= 95% de la mediana de 10 ruedas previas Y AUM reportado
    >= 98,5% del AUM esperado (último dato de cada clase con antigüedad <= 7 días, en ARS al A3500).
    Devuelve (última fecha con datos, última completa, n clases por día, cobertura de AUM por día)."""
    d = duckdb.sql(f"""select fecha, clase_id, aum from read_parquet('{base_dir}/clases_diario_*.parquet')
                       where aum is not null and fecha >= (select max(fecha) from read_parquet('{base_dir}/clases_diario_*.parquet')) - INTERVAL {dias} DAY""").df()
    d['fecha'] = pd.to_datetime(d.fecha)
    mon = pd.read_parquet(os.path.join(base_dir, 'clases_meta.parquet')).drop_duplicates('clase_id').set_index('clase_id').moneda
    fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), index_col=0, parse_dates=True).a3500
    P = d.pivot_table(index='fecha', columns='clase_id', values='aum')
    P = P[P.index.dayofweek < 5]
    usd = mon.reindex(P.columns).eq('USD').values
    P.loc[:, usd] = P.loc[:, usd].mul(fx.reindex(P.index).ffill(), axis=0)
    esperado = P.ffill(limit=7).sum(axis=1)       # con los ceros incluidos: una clase que pasó a 0 no se espera
    cov = P.sum(axis=1) / esperado
    n = (P > 0).sum(axis=1)
    ref = n.rolling(10, min_periods=3).median().shift(1)
    ok = n.index[(n >= min_n * ref) & (cov >= min_aum)]
    return n.index.max(), (ok.max() if len(ok) else n.index.max()), n, cov


def corte(base_dir):
    """Fecha de corte por defecto y su estado. Preliminar: el día más reciente con >= 85% de las clases y >= 80% del AUM
    esperado (la planilla de la CNV del día D llega a la noche incompleta; la revisada, al día hábil siguiente, la reemplaza)."""
    ult, completa, n, cov = fecha_corte(base_dir)
    ref = n.rolling(10, min_periods=3).median().shift(1)
    pre = n.index[(n >= PRE_N * ref) & (cov >= PRE_AUM) & (n.index > completa)]
    if MODO_CORTE == 'preliminar' and len(pre):
        return pre.max(), 'preliminar'
    return completa, 'definitivo'


SUSC = [f'susc_{h}_m' for h in HZ]
SUSC_U = [f'susc_usd_{h}_m' for h in HZ]   # vista en dólares: sólo clases USD, en USD


def es_iol(df):
    """IOL = fondos de Adcap cuyo nombre empieza con 'IOL ' (Cash Management, Dólar Ahorro Plus, Portafolio Potenciado)."""
    return df.gerente.eq('Adcap') & df.fondo.fillna('').str.startswith('IOL ')


def split_iol(df):
    g = df.gerente.copy()
    i = es_iol(df)
    g[i] = 'IOL'
    g[df.gerente.eq('Adcap') & ~i] = 'Adcap ex IOL'
    return g


def ger_seg(snap):
    """gerente x segmento en formato largo (Total / tipo / subtipo): AUM, share y posición en el segmento, suscripciones."""
    gl = []
    for nivel, key in (('Total', None), ('Tipo', 'tipo'), ('Subtipo', 'subtipo')):
        x = snap.assign(segmento='Total' if key is None else snap[key], aum_usd_cls=np.where(snap.moneda == 'USD', snap.aum, 0.0))
        if key == 'subtipo':
            x = x[x.segmento != x.tipo]          # T+1 / Sin clasificar: subtipo == tipo, ya están como Tipo
        g = x.groupby(['segmento', 'gerente', 'tipo_gerente']).agg(
            aum_m=('aum_ars', 'sum'), aum_usd_m=('aum_usd_cls', 'sum'), n_fondos=('fondo_act', 'nunique'),
            **{f'susc_{h}_m': (f'f_{h}', 'sum') for h in HZ},
            **{f'aum0_{h}_m': (f'aum0_{h}', 'sum') for h in HZ},
            **{f'susc_usd_{h}_m': (f'fu_{h}', 'sum') for h in HZ},
            **{f'aum0_usd_{h}_m': (f'aum0u_{h}', 'sum') for h in HZ}).reset_index()
        for c in ['aum_m', 'aum_usd_m'] + SUSC + [f'aum0_{h}_m' for h in HZ] + SUSC_U + [f'aum0_usd_{h}_m' for h in HZ]:
            g[c] = g[c] / 1e6
        for h in HZ:                               # suscripciones como % del AUM al inicio del plazo (vacío si < ARS 1 M)
            g[f'susc_{h}_pct'] = (g[f'susc_{h}_m'] / g[f'aum0_{h}_m']).where(g[f'aum0_{h}_m'] >= 1)
            # vista en dólares: sólo clases USD (vacío si el AUM inicial en USD < 10 mil)
            g[f'susc_usd_{h}_pct'] = (g[f'susc_usd_{h}_m'] / g[f'aum0_usd_{h}_m']).where(g[f'aum0_usd_{h}_m'] >= 0.01)
        g = g[g.aum_m > 0]
        g['share'] = g.aum_m / g.groupby('segmento').aum_m.transform('sum')
        g['pos'] = g.groupby('segmento').aum_m.rank(ascending=False, method='min').astype(int)
        g['nivel'] = nivel
        gl.append(g)
    GL = pd.concat(gl, ignore_index=True).sort_values(['nivel', 'segmento', 'aum_m'], ascending=[True, True, False])
    return GL[['nivel', 'segmento', 'pos', 'gerente', 'tipo_gerente', 'aum_m', 'share'] + SUSC + ['n_fondos'] +
              [f'aum0_{h}_m' for h in HZ] + [f'susc_{h}_pct' for h in HZ] + ['aum_usd_m'] +
              SUSC_U + [f'aum0_usd_{h}_m' for h in HZ] + [f'susc_usd_{h}_pct' for h in HZ]]
    # columnas nuevas siempre al final (el Sheet ubica por nombre). aum_usd_m: AUM de las clases USD en dólares, sin TC


def gerentes_con_mm(snap):
    """Gerentes con al menos un fondo Money Market (tipo 1816: MM ARS Clásico, MM ARS Dinámico o MM USD Clásico) con AUM > 0."""
    return set(snap.loc[snap.tipo.eq('Money Market') & (snap.aum_ars > 0), 'gerente'])


def compute(base_dir, out_dir, T=None, fx_col='a3500'):
    os.makedirs(out_dir, exist_ok=True)
    d, m, fx = load(base_dir)
    ult, completa, ncl, cov = fecha_corte(base_dir)
    Td, estado = corte(base_dir)
    if T:
        T = pd.Timestamp(T)
        estado = 'definitivo' if T <= completa else 'preliminar'
    else:
        T = Td
    m = m[~m.subtipo.isin(['Fuera de universo'])].copy()
    d = d[d.clase_id.isin(m.clase_id) & (d.fecha <= T)]
    mon = m.set_index('clase_id').moneda
    d['usd'] = d.clase_id.map(mon).eq('USD')
    d = d.join(fx[[fx_col]].rename(columns={fx_col: 'fx'}), on='fecha')
    d['k'] = np.where(d.usd, d.fx, 1.0)
    d['flujo_ars'] = d.flujo * d.k
    # ---- foto a T por clase
    # último patrimonio informado de cada clase en la semana (incluido 0): una clase que pasó a 0 no arrastra su AUM
    # anterior (en un cambio de clase, p. ej. Lombard Ahorro Plus A -> B el 17/11, el fondo se contaba dos veces 7 días)
    last = d[(d.fecha > T - pd.Timedelta(days=7)) & d.aum.notna()].sort_values('fecha').groupby('clase_id').tail(1)
    last = last[last.aum > 0]
    # clases que se vaciaron dentro del plazo más largo: entran con AUM 0 para que sus rescates cuenten (si no, un
    # cambio de clase dentro de un fondo suma la suscripción de la clase nueva y pierde el rescate de la vieja)
    hmax = base_date(T, 365)
    viejas = d[(d.fecha > hmax) & d.flujo.abs().gt(0) & ~d.clase_id.isin(last.clase_id)].clase_id.unique()
    vacias = d[d.clase_id.isin(viejas)].sort_values('fecha').groupby('clase_id').tail(1).assign(aum=0.0)
    last = pd.concat([last, vacias], ignore_index=True)
    snap = last[['clase_id', 'fecha', 'vcp', 'aum']].rename(columns={'fecha': 'fecha_dato'})
    snap['stale'] = (snap.fecha_dato < T) & (snap.aum > 0)
    fxT = fx.loc[T]
    snap = snap.merge(m, on='clase_id', how='left')
    snap['fondo_act'] = snap.fondo_id.where(snap.aum > 0)
    snap['aum_ars'] = np.where(snap.moneda == 'USD', snap.aum * fxT[fx_col], snap.aum)
    for c in ('a3500', 'mep', 'ccl'):
        snap[f'aum_ars_{c}'] = np.where(snap.moneda == 'USD', snap.aum * fxT[c], snap.aum)
    # ---- rendimientos por clase (moneda de la clase) y en pesos
    vser = d.dropna(subset=['vcp']).sort_values('fecha')
    S0 = snap.set_index('clase_id')
    is_usd = (S0.moneda == 'USD').values
    rows = {}
    for h, v in HZ.items():
        b = base_date(T, v)
        vb = vser[vser.fecha <= b].groupby('clase_id').tail(1).set_index('clase_id').vcp.reindex(S0.index)
        r = S0.vcp / vb - 1
        quiebre = d[(d.fecha > b) & (d.fecha <= T) & (d.flag == 2)].clase_id.unique()
        r[r.index.isin(quiebre)] = np.nan          # no se mide rendimiento a través de un rebase
        fxb = fx[fx.index <= b].iloc[-1]
        rows[f'r_{h}'] = r
        rows[f'rars_{h}'] = (1 + r) * np.where(is_usd, fxT[fx_col] / fxb[fx_col], 1.0) - 1
        rows[f'f_{h}'] = d[(d.fecha > b) & (d.fecha <= T)].groupby('clase_id').flujo_ars.sum().reindex(S0.index)
        # AUM al inicio del plazo: último patrimonio informado hasta la fecha base (puede ser 0). Si es de más de 10 días antes,
        # la clase no estaba activa en la base (antes se tomaba el último > 0 sin límite: clases que dejaron de informar
        # patrimonio hace meses inflaban el AUM inicial, p. ej. IAM Retorno Dólares A)
        ab = d[(d.fecha <= b) & d.aum.notna()].sort_values('fecha').groupby('clase_id').tail(1).set_index('clase_id')
        ab = ab.aum.where(ab.fecha >= b - pd.Timedelta(days=10), 0.0).reindex(S0.index)
        rows[f'aum0_{h}'] = ab * np.where(is_usd, fxb[fx_col], 1.0)
        # vista en dólares: sólo clases USD, en su moneda (sin tipo de cambio)
        fc = d[(d.fecha > b) & (d.fecha <= T)].groupby('clase_id').flujo.sum().reindex(S0.index)
        rows[f'fu_{h}'] = fc.where(is_usd)
        rows[f'aum0u_{h}'] = ab.where(is_usd)
    R = pd.DataFrame(rows, index=S0.index)
    snap = snap.merge(R, left_on='clase_id', right_index=True, how='left')
    # volatilidad 90d anualizada y rend/vol (como 1816)
    w = d[(d.fecha > T - pd.Timedelta(days=90))].pivot_table(index='fecha', columns='clase_id', values='rend')
    vol = w.std() * np.sqrt(252)
    snap['vol_90d'] = snap.clase_id.map(vol)
    snap['r_90d_anual'] = (1 + snap.r_90d) ** (365 / 90) - 1
    snap['rend_vol'] = snap.r_90d_anual / snap.vol_90d
    snap['hon_gd'] = snap.hon_gerente.fillna(0) + snap.hon_depositaria.fillna(0)
    snap['fecha_corte'] = T
    # ---------------- agregados por segmento (estilo 1816 p.13)
    def agg(g):
        o = {'aum_ars_m': g.loc[g.moneda == 'ARS', 'aum_ars'].sum() / 1e6, 'aum_usd_m': g.loc[g.moneda == 'USD', 'aum'].sum() / 1e6,
             'aum_total_m': g.aum_ars.sum() / 1e6}
        for h in HZ:
            o[f'susc_{h}_m'] = g[f'f_{h}'].sum() / 1e6
        for h in ('YTD', '365d'):
            a0 = np.nansum(g[f'aum0_{h}'])
            o[f'susc_{h}_pct_aum'] = g[f'f_{h}'].sum() / a0 if a0 >= 0.05 * g.aum_ars.sum() and a0 > 0 else np.nan
        wts = g.aum_ars.where(g.aum_ars > 0)
        for h in HZ:
            x = g[f'rars_{h}']; ok = x.notna() & wts.notna()
            o[f'rend_{h}'] = np.average(x[ok], weights=wts[ok]) if ok.any() else np.nan
        # vista en dólares (sólo clases USD, sin tipo de cambio): suscripciones, % del AUM inicial y rendimiento en USD
        u = g[g.moneda == 'USD']; wu = u.aum.where(u.aum > 0)
        for h in HZ:
            o[f'susc_usd_{h}_m'] = u[f'fu_{h}'].sum() / 1e6
            x = u[f'r_{h}']; ok = x.notna() & wu.notna()
            o[f'rend_usd_{h}'] = np.average(x[ok], weights=wu[ok]) if ok.any() else np.nan
        for h in ('YTD', '365d'):
            a0 = np.nansum(u[f'aum0u_{h}'])
            o[f'susc_usd_{h}_pct_aum'] = u[f'fu_{h}'].sum() / a0 if a0 >= 0.05 * u.aum.sum() and a0 > 0 else np.nan
        o['n_fondos'] = g.fondo_act.nunique(); o['n_clases'] = int((g.aum > 0).sum())
        o['n_fondos_usd'] = u.fondo_act.nunique()
        o['aum_stale_pct'] = g.loc[g.stale, 'aum_ars'].sum() / g.aum_ars.sum() if g.aum_ars.sum() else np.nan
        return pd.Series(o)
    seg = snap.groupby(['tipo', 'subtipo']).apply(agg).reset_index()
    tip = snap.groupby('tipo').apply(agg).reset_index().assign(subtipo='Total')
    tot = pd.DataFrame([agg(snap)]).assign(tipo='Total', subtipo='Total')
    S = pd.concat([tot, tip, seg], ignore_index=True)
    S['pct_total'] = S.aum_total_m / tot.aum_total_m.iloc[0]
    S['ord_t'] = S.tipo.map({t: i for i, t in enumerate(['Total'] + TIPOS)}).fillna(99)
    S['ord_s'] = S.subtipo.map({s: i for i, s in enumerate(['Total'] + ORDEN)}).fillna(99)
    S = S.sort_values(['ord_t', 'ord_s']).drop(columns=['ord_t', 'ord_s'])
    # ---------------- gerentes
    G = snap.groupby(['gerente', 'tipo_gerente']).apply(agg).reset_index().sort_values('aum_total_m', ascending=False)
    G['share'] = G.aum_total_m / G.aum_total_m.sum()
    GT = snap.groupby(['gerente', 'tipo']).aum_ars.sum().unstack(fill_value=0) / 1e6
    GS = snap.groupby(['gerente', 'subtipo']).aum_ars.sum().unstack(fill_value=0) / 1e6
    TG = snap.groupby('tipo_gerente').apply(agg).reset_index()
    GL = ger_seg(snap)
    SP = snap.assign(gerente=split_iol(snap), tipo_gerente=np.where(es_iol(snap), 'Independiente', snap.tipo_gerente))
    GLI = ger_seg(SP)
    con_mm = gerentes_con_mm(SP)                       # universo sin gerentes con Money Market (IOL como gerente separado)
    GLM = ger_seg(SP[~SP.gerente.isin(con_mm)])
    # ---------------- ranking: un fondo = una fila (su clase de referencia); umbral AUM fondo >= 1.000 M ARS, como 1816
    snap['aum_fondo_ars'] = snap.groupby('fondo_id').aum_ars.transform('sum')
    snap['n_clases_fondo'] = snap.aum.gt(0).groupby(snap.fondo_id).transform('sum')
    for h in HZ:                                   # suscripciones y AUM inicial del fondo completo (todas sus clases)
        snap[f'ffondo_{h}'] = snap.groupby('fondo_id')[f'f_{h}'].transform(lambda x: x.sum(min_count=1))
        snap[f'aum0fondo_{h}'] = snap.groupby('fondo_id')[f'aum0_{h}'].transform(lambda x: x.sum(min_count=1))
        snap[f'ffondo_usd_{h}'] = snap.groupby('fondo_id')[f'fu_{h}'].transform(lambda x: x.sum(min_count=1))
        snap[f'aum0fondo_usd_{h}'] = snap.groupby('fondo_id')[f'aum0u_{h}'].transform(lambda x: x.sum(min_count=1))
    act = snap.aum > 0
    ref = snap.loc[act & (snap.clase_id == snap.ref_clase)].drop_duplicates('fondo_id').set_index('fondo_id').clase_id
    sin = snap.loc[act & ~snap.fondo_id.isin(ref.index)]  # la clase de referencia no tiene dato a T: regla sobre las activas
    if len(sin):
        import maestro
        alt = maestro.regla_ref(sin, float(fxT[fx_col]))
        ref = pd.concat([ref, alt])
        snap.loc[snap.fondo_id.isin(alt.index), 'ref_origen'] = 'regla (ref. sin dato)'
    snap['es_ref'] = snap.clase_id.isin(ref.values)
    # todos los fondos (el Sheet filtra por AUM mínimo); posiciones de rendimiento dentro de fondos >= 1.000 M, como 1816
    RK = snap[snap.es_ref].copy()
    big = RK.aum_fondo_ars >= 1e9
    for h in HZ:
        RK[f'pos_{h}'] = RK[big].groupby('subtipo')[f'r_{h}'].rank(ascending=False, method='min')
    # ---------------- serie diaria de industria (últimos 400 días)
    dd = d[d.fecha > T - pd.Timedelta(days=400)].merge(m[['clase_id', 'tipo', 'subtipo']], on='clase_id')
    dd['aum_ars'] = dd.aum * dd.k
    full = dd.pivot_table(index='fecha', columns='clase_id', values='aum_ars').ffill(limit=5)
    tipo_map = m.set_index('clase_id').tipo
    aum_tipo = full.T.groupby(full.columns.map(tipo_map)).sum().T
    aum_tipo['Total'] = aum_tipo.sum(axis=1)
    fl_tipo = dd.groupby(['fecha', 'tipo']).flujo_ars.sum().unstack(fill_value=0); fl_tipo['Total'] = fl_tipo.sum(axis=1)
    bd = aum_tipo.index[aum_tipo.index.dayofweek < 5]
    meta_out = dict(fecha_corte=str(T.date()), ultima_fecha_datos=str(ult.date()), ultima_fecha_completa=str(completa.date()),
                    clases_con_dato_T=int(ncl.get(T, 0)), estado_corte=estado, cobertura_aum_T=float(cov.get(T, np.nan)), aum_stale_pct=float(snap.loc[snap.stale, 'aum_ars'].sum() / snap.aum_ars.sum()),
                    gerentes_con_mm=len(con_mm), fx=fx_col, fx_T={c: float(fxT[c]) for c in ('a3500', 'mep', 'ccl')})
    return dict(seg=S, ger=G, ger_tipo=GT, ger_sub=GS, ger_seg=GL, ger_iol=GLI, ger_iol_sin_mm=GLM, tipo_ger=TG, ranking=RK, snap=snap,
                aum_diario=aum_tipo.loc[bd] / 1e6, flujo_diario=fl_tipo.reindex(bd).fillna(0) / 1e6, meta=meta_out)


if __name__ == '__main__':
    res = compute(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    print(json.dumps(res['meta'], indent=1))
    pd.set_option('display.width', 250)
    print(res['seg'][['tipo', 'subtipo', 'aum_ars_m', 'aum_usd_m', 'aum_total_m', 'susc_1d_m', 'susc_30d_m', 'susc_YTD_m', 'rend_30d', 'rend_YTD']].round(3).to_string())
