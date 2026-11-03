"""Tabla maestra: clase -> fondo -> gerente (corto) -> tipo de gerente -> segmento 1816.

Fuentes (en orden de prioridad):
  1. ref/segmento_manual.csv   : correcciones manuales por fondo (fondo_id, subtipo)  [lo edita el usuario]
  2. ref/map1816.csv           : clases listadas en el informe 1816 (nombre de cuota, gerente, AUM) -> fondo completo
  3. regla por categoría CAFCI : fondos de categoría unívoca (Mercado de Dinero, PyMEs, Infraestructura, ASG)
  4. correlación de rendimientos: fondo sin clasificar -> segmento cuyo rendimiento diario (120 ruedas) más se parece
Cada fondo queda con 'origen_segmento' para auditar.
"""
import os, re, sys, unicodedata, difflib
import numpy as np, pandas as pd

TIPO = {'MM ARS Clásico': 'Money Market', 'MM ARS Dinámico': 'Money Market', 'MM USD Clásico': 'Money Market', 'T+1': 'T+1',
        'RF ARS Lecap': 'Renta Fija', 'RF ARS CER': 'Renta Fija', 'RF ARS Disc': 'Renta Fija', 'RF MULT Corp': 'Renta Fija',
        'RF MULT Disc': 'Renta Fija', 'RF USD Linked': 'Renta Fija', 'RF USD CP': 'Renta Fija', 'RF USD corp loc': 'Renta Fija',
        'RF USD Disc': 'Renta Fija', 'RF USD GL Inc ARG': 'Renta Fija', 'RF USD GL Ex ARG': 'Renta Fija',
        'RV ARG Merval': 'Renta Variable', 'RV ARG Rofex 20': 'Renta Variable', 'RV ARG Otros': 'Renta Variable', 'RV Global': 'Renta Variable',
        'RM y Otros ARS': 'Renta Mixta', 'RM y Otros USD': 'Renta Mixta', 'PYME': 'Destino Esp.', 'INFRA': 'Destino Esp.', 'ASG': 'Destino Esp.'}
# candidatos por (categoria CAFCI, moneda) para la clasificación por correlación
CAND = {
    ('Renta Fija', 'ARS'): ['T+1', 'RF ARS Lecap', 'RF ARS CER', 'RF ARS Disc', 'RF MULT Corp', 'RF MULT Disc', 'RF USD Linked'],
    ('Renta Fija', 'USD'): ['RF USD CP', 'RF USD corp loc', 'RF USD Disc', 'RF USD GL Inc ARG', 'RF USD GL Ex ARG'],
    ('Renta Variable', 'ARS'): ['RV ARG Merval', 'RV ARG Rofex 20', 'RV ARG Otros'],
    ('Renta Variable', 'USD'): ['RV Global'],
    ('Renta Mixta', 'ARS'): ['RM y Otros ARS'], ('Renta Mixta', 'USD'): ['RM y Otros USD'],
    ('Retorno Total', 'ARS'): ['RM y Otros ARS'], ('Retorno Total', 'USD'): ['RM y Otros USD'],
    ('Mercado de Dinero', 'ARS'): ['MM ARS Clásico'], ('Mercado de Dinero', 'USD'): ['MM USD Clásico'],
    ('PyMEs', 'ARS'): ['PYME'], ('PyMes', 'ARS'): ['PYME'], ('Infraestructura', 'ARS'): ['INFRA'], ('Infraestructura', 'USD'): ['INFRA'], ('ASG', 'ARS'): ['ASG'],
}
EXCLUIR = ('Fondos Cerrados', 'Cerrado', 'RG900')  # fuera del universo 1816 (fondos abiertos)


def norm(s):
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode().lower()
    s = re.sub(r'\s*-\s*clase\s*', ' ', s)
    s = re.sub(r'\bclase\b', ' ', s)
    s = re.sub(r'[^a-z0-9+ ]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def short_admin(a):
    s = re.sub(r'(S\.?G\.?F\.?C\.?I\.?|S\.?A\.?U?\.?|Sociedad Gerente.*|Asset Management|Administradora.*|de Fondos.*|Fondos Comunes.*|Gestion|Gestión|Argentina)', '', str(a), flags=re.I)
    return re.sub(r'\s+', ' ', s).strip(' .,-') or str(a)


FX1816 = 1613.0  # tipo de cambio implícito del informe 1816 al 02/11/2026 (AUM USD -> ARS); solo para validar AUM
SYN = [(r'\bctio\b', 'consultatio'), (r'\bu ?s ?s\b|\busd\b|\bdolares\b|\bdol\b', 'dolar'), (r'\$', ' pesos '),
       (r'\binfraestr\w*|\binfra\b', 'infraestructura'), (r'\bfcia?\b', ' '), (r'\bd\s*99\b', ' ')]


def norm2(s):
    s = norm(str(s).replace('$', ' pesos '))
    for a, b in SYN:
        s = re.sub(a, b, s)
    return re.sub(r'\s+', ' ', s).strip()


def aum_err(aum, aum16):
    if not np.isfinite(aum) or aum16 <= 0:
        return np.inf
    return min(abs(v - aum16) / aum16 for v in (aum, aum / FX1816, aum * FX1816))


def match_1816(meta, last, m16):
    """Empareja cada cuota del informe con una clase.
    1) nombre normalizado exacto; 2) dentro de las administradoras del mismo gerente: misma letra de clase y nombre parecido
    (o prefijo si el nombre viene truncado con '..'); siempre se elige la de AUM más cercano (con conversión ARS/USD)."""
    c = meta.merge(last, on='clase_id', how='left')
    c['key'] = c.clase.map(norm2)
    c['letra'] = c.clase.str.extract(r'Clase\s+([A-Z0-9]+)', expand=False)
    m16 = m16.copy(); m16['key'] = m16.cuota.map(norm2)
    out = []
    # paso 1: exactos
    for i, r in m16.iterrows():
        cand = c[c.key == r.key]
        if len(cand):
            e = cand.aum_ref.apply(lambda v: aum_err(v, r.aum_cuota_m * 1e6))
            j = e.idxmin()
            out.append(dict(i=i, clase_id=c.loc[j, 'clase_id'], como='exacto', err_aum=e[j]))
    ex = pd.DataFrame(out)
    adm = ex.merge(c[['clase_id', 'administradora']], on='clase_id').assign(g=lambda d: m16.loc[d.i, 'gerente'].values)
    g2adm = adm.groupby('g').administradora.apply(lambda s: set(s)).to_dict()
    done = set(ex.i) if len(ex) else set()
    for i, r in m16.iterrows():
        if i in done:
            continue
        pool = c[c.administradora.isin(g2adm.get(r.gerente, set()))]
        trunc = str(r.cuota).endswith('..')
        letra = None if trunc else (str(r.cuota).split()[-1] if len(str(r.cuota).split()) > 1 else None)
        base_key = norm2(str(r.cuota).rstrip('.'))
        if trunc:
            cand = pool[pool.key.str.startswith(base_key[:max(8, len(base_key) - 2)])]
        else:
            stem = ' '.join(base_key.split()[:-1])
            cand = pool[(pool.letra == letra)] if letra and len(letra) <= 2 else pool
            cand = cand.assign(sim=cand.key.map(lambda k: difflib.SequenceMatcher(None, ' '.join(k.split()[:-1]), stem).ratio()))
            cand = cand[cand.sim >= 0.6]
        if cand.empty:
            out.append(dict(i=i, clase_id=None, como='sin match', err_aum=np.nan)); continue
        e = cand.aum_ref.apply(lambda v: aum_err(v, r.aum_cuota_m * 1e6))
        j = e.idxmin()
        ok = e[j] < 0.20
        out.append(dict(i=i, clase_id=c.loc[j, 'clase_id'] if ok else None, como=('prefijo' if trunc else 'aprox') if ok else 'sin match (AUM)', err_aum=e[j]))
    res = pd.DataFrame(out).set_index('i').sort_index()
    return m16.drop(columns=['key']).join(res)


def corr_classify(base, meta_f, seg_ref, unclassified, window=120):
    """Asigna segmento por mínima distancia (RMSE) entre rendimientos diarios del fondo y del segmento."""
    piv = base.pivot_table(index='fecha', columns='clase_id', values='rend')
    piv = piv.iloc[-window:]
    seg_series = {}
    for seg, ids in seg_ref.items():
        cols = [i for i in ids if i in piv.columns]
        if cols:
            seg_series[seg] = piv[cols].median(axis=1)
    res = []
    for _, f in unclassified.iterrows():
        cands = CAND.get((f.tipo_renta, f.moneda), [])
        if len(cands) == 1:                      # categoría unívoca: no hace falta correlación
            res.append((f.fondo_id, cands[0], 0.0)); continue
        cands = [s for s in cands if s in seg_series]
        if f.ref_clase not in piv.columns or not cands:
            res.append((f.fondo_id, None, None)); continue
        x = piv[f.ref_clase]
        if x.count() < 40:
            res.append((f.fondo_id, None, None)); continue
        d = {s: float(np.sqrt(((x - seg_series[s]) ** 2).mean())) for s in cands}
        best = min(d, key=d.get)
        res.append((f.fondo_id, best, d[best]))
    return pd.DataFrame(res, columns=['fondo_id', 'subtipo', 'rmse'])


def build(base_dir, ref_dir, fecha_ref):
    meta = pd.read_parquet(os.path.join(base_dir, 'clases_meta.parquet'))
    import duckdb
    con = duckdb.connect()
    last = con.sql(f"""select clase_id, arg_max(aum, fecha) aum_ref from read_parquet('{base_dir}/clases_diario_*.parquet')
                       where fecha <= '{fecha_ref}' and fecha >= cast('{fecha_ref}' as date) - interval 10 day and aum > 0 group by 1""").df()
    m16 = pd.read_csv(os.path.join(ref_dir, 'map1816.csv'))
    mt = match_1816(meta, last, m16)
    # gerente corto: por administradora (moda de las clases emparejadas)
    mm = mt.dropna(subset=['clase_id']).merge(meta[['clase_id', 'fondo_id', 'administradora']], on='clase_id')
    adm_short = mm.groupby('administradora').gerente.agg(lambda s: s.value_counts().index[0]).to_dict()
    meta['gerente'] = meta.administradora.map(adm_short).fillna(meta.administradora.map(short_admin))
    tipo_g = pd.read_csv(os.path.join(ref_dir, 'gerente_tipo_1816.csv')).set_index('gerente_1816').tipo_gerente.to_dict()
    man_g = os.path.join(ref_dir, 'gerente_tipo_manual.csv')
    if os.path.exists(man_g):
        tipo_g.update(pd.read_csv(man_g).set_index('gerente').tipo_gerente.to_dict())
    meta['tipo_gerente'] = meta.gerente.map(tipo_g).fillna('Sin dato')
    # segmento por fondo
    fondo_seg = mm.groupby('fondo_id').subtipo.agg(lambda s: s.value_counts().index[0])
    meta['subtipo'] = meta.fondo_id.map(fondo_seg)
    meta['origen_segmento'] = np.where(meta.subtipo.notna(), 'informe 1816', None)
    # fondos sin segmento: clase de referencia = la de mayor AUM
    meta = meta.merge(last, on='clase_id', how='left')
    ref = meta.sort_values('aum_ref', ascending=False).drop_duplicates('fondo_id')[['fondo_id', 'clase_id']].rename(columns={'clase_id': 'ref_clase'})
    meta = meta.merge(ref, on='fondo_id', how='left')
    fondos = meta.drop_duplicates('fondo_id')
    unc = fondos[fondos.subtipo.isna() & ~fondos.tipo_renta.isin(EXCLUIR)]
    seg_ref = mm.groupby('subtipo').clase_id.apply(list).to_dict()
    base = con.sql(f"""select fecha, clase_id, rend from read_parquet('{base_dir}/clases_diario_*.parquet')
                       where fecha > cast('{fecha_ref}' as date) - interval 200 day and fecha <= '{fecha_ref}'""").df()
    cc = corr_classify(base, fondos, seg_ref, unc)
    cc = cc.dropna(subset=['subtipo']).set_index('fondo_id')
    fill = meta.fondo_id.map(cc.subtipo)
    meta.loc[meta.subtipo.isna() & fill.notna(), 'origen_segmento'] = np.where(
        meta.loc[meta.subtipo.isna() & fill.notna(), 'fondo_id'].map(cc.rmse) == 0, 'regla CAFCI', 'correlación')
    meta['subtipo'] = meta.subtipo.fillna(fill)
    man = os.path.join(ref_dir, 'segmento_manual.csv')
    if os.path.exists(man):
        mn = pd.read_csv(man, dtype={'fondo_id': str}).set_index('fondo_id').subtipo
        hit = meta.fondo_id.isin(mn.index)
        meta.loc[hit, 'subtipo'] = meta.loc[hit, 'fondo_id'].map(mn); meta.loc[hit, 'origen_segmento'] = 'manual'
    meta.loc[meta.tipo_renta.isin(EXCLUIR), ['subtipo', 'origen_segmento']] = ['Fuera de universo', 'excluido']
    meta['subtipo'] = meta.subtipo.fillna('Sin clasificar')
    meta['tipo'] = meta.subtipo.map(TIPO).fillna(meta.subtipo)
    meta['fondo'] = meta.clase.str.replace(r'\s*-\s*Clase.*$', '', regex=True)
    return meta, mt


if __name__ == '__main__':
    meta, mt = build(sys.argv[1], sys.argv[2], sys.argv[3])
    meta.to_parquet(os.path.join(sys.argv[1], 'maestro.parquet'), index=False)
    mt.to_csv(os.path.join(sys.argv[2], 'match1816_log.csv'), index=False)
    print(mt.como.value_counts().to_dict())
    print(meta.drop_duplicates('fondo_id').origen_segmento.value_counts(dropna=False).to_dict())
