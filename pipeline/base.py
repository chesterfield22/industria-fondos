"""Construye la base histórica diaria por clase desde los archivos de ArgentinaDatos (réplica de la planilla diaria CNV).

Salida: data/base/clases_diario_<año>.parquet  (fecha, clase_id, vcp, aum, flujo, rend, flag)
        data/base/clases_meta.parquet         (metadatos por clase: fondo, gerente, moneda, honorarios, etc.)

Reglas de calidad (sin recortar historia válida):
  - flujo_t = AUM_t - AUM_{t-1} * VCP_t / VCP_{t-1}   (por clase, observaciones consecutivas)
  - se anula flujo y rendimiento del día si |rend| > umbral (10% ARS / 5% USD) o AUM_t/AUM_{t-1} ~ x1000 (error de unidad)
  - clases duplicadas (mismo claseId en dos slugs) -> se conserva la serie más reciente/larga
"""
import json, glob, os, sys
import numpy as np, pandas as pd

RMAX = {'ARS': 0.10, 'USD': 0.05}
MONEDA = {'Peso Argentino': 'ARS', 'Dólar Estadounidense': 'USD', 'Dolar Estadounidense': 'USD',
          'Dólar Estadounidense Billete': 'USD', 'Real': 'BRL', 'Euro': 'EUR'}


def read_class(dirpath):
    idx_p, his_p = os.path.join(dirpath, 'index.json'), os.path.join(dirpath, 'historico', 'index.json')
    if not os.path.exists(his_p):
        return None, None
    h = json.load(open(his_p, encoding='utf-8'))
    meta = json.load(open(idx_p, encoding='utf-8')) if os.path.exists(idx_p) else {}
    rows = h.get('historico', [])
    if not rows:
        return None, None
    df = pd.DataFrame(rows)
    df['fecha'] = pd.to_datetime(df['fecha'].astype(str).str[:10], errors='coerce')
    df = df.dropna(subset=['fecha']).sort_values('fecha').drop_duplicates('fecha', keep='last')
    df = df[['fecha', 'valorCuotaparte', 'patrimonio']].rename(columns={'valorCuotaparte': 'vcp', 'patrimonio': 'aum'})
    df['vcp'] = pd.to_numeric(df.vcp, errors='coerce'); df['aum'] = pd.to_numeric(df.aum, errors='coerce')
    hon = meta.get('honorarios') or {}
    last = rows[-1]
    m = dict(slug=os.path.basename(dirpath), clase_id=str(h.get('claseId') or meta.get('claseId')),
             fondo_id=str(h.get('fondoId') or meta.get('fondoId')), clase=h.get('nombre') or meta.get('nombre'),
             administradora=meta.get('administradora'), depositaria=meta.get('depositaria'),
             tipo_renta=meta.get('tipoRenta') or last.get('categoria'), region=meta.get('region'),
             horizonte=meta.get('horizonte') or last.get('horizonte'), benchmark=meta.get('benchmark'),
             moneda_raw=meta.get('moneda'), plazo_liq=meta.get('plazoLiquidacionDias'),
             inversion_min=meta.get('inversionMinima'), codigo_cnv=meta.get('codigoCNV'),
             hon_gerente=hon.get('honorarioGerente'), hon_depositaria=hon.get('honorarioDepositaria'),
             gastos=hon.get('gastosOrdinariosGestion'), com_ingreso=hon.get('comisionIngreso'),
             com_egreso=hon.get('comisionEgreso'), fecha_ultima=df.fecha.max(), n_obs=len(df))
    return m, df


def _spikes(x, L, back, k_max=5):
    """Índices de picos transitorios: salto |log| > L que vuelve (|log| < back respecto del nivel previo) en <= k_max obs."""
    lx = np.log(x)
    j = np.diff(lx, prepend=np.nan)
    drop = np.zeros(len(x), bool); brk = np.zeros(len(x), bool)
    for t in np.where(np.abs(j) > L)[0]:
        if drop[t] or t == 0:
            continue
        prev = lx[t - 1]
        for k in range(1, k_max + 1):
            if t + k < len(x) and abs(lx[t + k] - prev) < back:
                drop[t:t + k] = True
                break
        else:
            brk[t] = True
    return drop, brk


def fix_currency(d, moneda, fx):
    """Corrige tramos de AUM informados en otra moneda: saltos de AUM cuyo tamaño ~ tipo de cambio (A3500 +-35%).
    Se normaliza cada tramo a la moneda de la clase. Devuelve d corregido y cantidad de tramos corregidos."""
    a = d.aum.values.astype(float)
    if len(a) < 6 or fx is None or moneda != 'USD':   # solo clases USD: el error observado es AUM informado en pesos
        return d, 0
    f = fx.reindex(d.fecha).ffill().bfill().values
    lq = np.log(np.where(a > 0, a, np.nan)); j = np.diff(lq, prepend=np.nan)
    cut = np.where(np.abs(np.abs(j) - np.log(f)) < np.log(1.35))[0]
    bounds = np.r_[0, cut, len(a)]
    if len(cut) and np.diff(bounds).min() < 3:     # tramos de < 3 obs: los maneja el filtro de picos
        cut = np.array([c for c, l, r in zip(cut, np.diff(bounds)[:-1], np.diff(bounds)[1:]) if l >= 3 and r >= 3])
    if len(cut) == 0:
        return d, 0
    seg = np.zeros(len(a), int)
    for c in cut:
        seg[c:] += 1
    # nivel relativo de cada tramo: +1 si está "en pesos" respecto del anterior
    lvl = np.zeros(seg.max() + 1)
    for k, c in enumerate(cut, start=1):
        lvl[k] = lvl[k - 1] + (1 if j[c] > 0 else -1)
    # la moneda correcta: USD -> el nivel más bajo; ARS -> el nivel más alto
    target = lvl.min() if moneda == 'USD' else lvl.max()
    adj = (lvl[seg] - target)          # cuántas veces hay que dividir (+) o multiplicar (-) por FX
    d = d.copy(); d['aum'] = a / np.power(f, adj)
    return d, len(cut)


def clean_flows(df, moneda, fx=None):
    """Limpieza:
    1) picos transitorios de VCP (salto >40% que revierte en <=5 ruedas) o de AUM (x5 que revierte) -> se elimina la observación
    2) saltos permanentes de VCP >40% (rebase/cambio de unidad) -> 'quiebre' (flag=2): sin rendimiento ni flujo ese día, y
       los rendimientos por horizonte que lo crucen se anulan
    3) saltos diarios > 10% ARS / 5% USD que no son picos -> flag=1: se anula flujo y rendimiento de ese día
    4) AUM x1000 / /1000 -> flag=1"""
    d = df.dropna(subset=['vcp']).copy()
    d = d[d.vcp > 0].reset_index(drop=True)
    d, ncur = fix_currency(d, moneda, fx)
    if len(d) > 2:
        dv, bv = _spikes(d.vcp.values, np.log(1.4), np.log(1.1))
        a = d.aum.where(d.aum > 0).values
        ok = ~np.isnan(a)
        da = np.zeros(len(d), bool)
        if ok.sum() > 2:
            da_ok, _ = _spikes(a[ok], np.log(5), np.log(1.5))
            da[np.where(ok)[0]] = da_ok
        brk_dates = set(d.fecha[bv & ~dv])
        d = d[~(dv | da)].reset_index(drop=True)
    else:
        brk_dates = set()
    d['rend'] = d.vcp / d.vcp.shift(1) - 1
    d['flujo'] = d.aum - d.aum.shift(1) * (1 + d.rend)
    q = d.aum / d.aum.shift(1)
    bad = (d.rend.abs() > RMAX.get(moneda, 0.05)) | q.between(900, 1100) | q.between(1 / 1100, 1 / 900)
    d['flag'] = np.where(d.fecha.isin(brk_dates), 2, np.where(bad, 1, 0)).astype('int8')
    d.loc[d.flag > 0, ['flujo', 'rend']] = np.nan
    return d


def build(src_fondos, out_dir, fx_csv=None):
    os.makedirs(out_dir, exist_ok=True)
    fx = pd.read_csv(fx_csv, index_col=0, parse_dates=True).a3500 if fx_csv and os.path.exists(fx_csv) else None
    metas, parts = [], {}
    dirs = sorted(glob.glob(os.path.join(src_fondos, '*')))
    for i, dp in enumerate(dirs):
        m, df = read_class(dp)
        if m is None:
            continue
        m['moneda'] = MONEDA.get(m['moneda_raw'], 'USD' if 'lar' in str(m['moneda_raw']) else 'ARS')
        k = m['clase_id']
        if k in parts and (parts[k][1].fecha.max(), len(parts[k][1])) >= (df.fecha.max(), len(df)):
            continue
        parts[k] = (m, df)
    allrows = []
    for k, (m, df) in parts.items():
        d = clean_flows(df, m['moneda'], fx)
        d['clase_id'] = k
        allrows.append(d)
        metas.append(m)
    base = pd.concat(allrows, ignore_index=True)
    base['clase_id'] = base.clase_id.astype('string')
    meta = pd.DataFrame(metas)
    for y, g in base.groupby(base.fecha.dt.year):
        g.drop(columns=[]).to_parquet(os.path.join(out_dir, f'clases_diario_{y}.parquet'), index=False, compression='zstd')
    meta.to_parquet(os.path.join(out_dir, 'clases_meta.parquet'), index=False)
    print(f'clases={len(meta)} filas={len(base):,} desde={base.fecha.min().date()} hasta={base.fecha.max().date()} '
          f'flag1={int((base.flag == 1).sum())} quiebres={int((base.flag == 2).sum())}')
    return base, meta


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
