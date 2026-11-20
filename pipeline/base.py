"""Construye la base histórica diaria por clase: CNV (planilla diaria, fuente principal para los días recientes)
sobre la historia de ArgentinaDatos (o la base anterior publicada, si ArgentinaDatos no está disponible).

Salida: data/base/clases_diario_<año>.parquet  (fecha, clase_id, vcp, aum, flujo, rend, flag)
        data/base/clases_meta.parquet         (metadatos por clase: fondo, gerente, moneda, honorarios, etc.)

Reglas de calidad (sin recortar historia válida):
  - flujo_t = AUM_t - AUM_{t-1} * VCP_t / VCP_{t-1}   (por clase, observaciones consecutivas)
  - días sin patrimonio informado (hasta 5 ruedas) no cortan la serie de flujos: el flujo se mide contra el último AUM
    informado; un hueco más largo es una clase vacía y el rescate se registra el primer día sin patrimonio
  - primer dato de una clase nueva (posterior al inicio de la historia): flujo = AUM (todo es suscripción). Así un cambio
    de clase dentro del fondo (p. ej. Lombard Renta en Pesos A -> B el 17/11/2026) neta ~0 en vez de un rescate falso
  - se anula flujo y rendimiento del día si |rend| > umbral (10% ARS / 5% USD) o AUM_t/AUM_{t-1} ~ x1000 (error de unidad)
  - clases duplicadas (mismo claseId en dos slugs) -> se conserva la serie más reciente/larga
"""
import json, glob, os, sys
import numpy as np, pandas as pd

RMAX = {'ARS': 0.10, 'USD': 0.05}
INICIO_HISTORIA = pd.Timestamp('2020-01-10')
GAP_MAX = 5          # ruedas sin patrimonio que se puentean; más largo = clase vacía (AUM 0)


def _aum_flujos(aum):
    """AUM para medir flujos: huecos (NaN) de más de GAP_MAX ruedas después del primer dato -> 0."""
    a = aum.copy()
    nan = a.isna().values
    if not nan.any() or nan.all():
        return a
    first = int(np.argmax(~nan))
    i, n = first, len(a)
    while i < n:
        if nan[i]:
            j = i
            while j < n and nan[j]:
                j += 1
            if j - i > GAP_MAX:
                a.iloc[i:j] = 0.0
            i = j
        else:
            i += 1
    return a   # la historia arranca el 02/01/2020: antes de esto, primer dato != clase nueva
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


MIN_TRAMO_USD = 2e4   # un salto ~xFX desde (o hacia) un nivel menor a USD 20 mil es una suscripción/rescate real, no moneda


def fix_currency(d, moneda, fx, anclado=False):
    """Corrige tramos de AUM informados en otra moneda: saltos de AUM cuyo tamaño ~ tipo de cambio (A3500 +-35%).
    - Un salto desde/hacia un nivel < USD 20 mil no se toma como cambio de moneda (clase casi vacía que recibe o
      devuelve dinero real: p. ej. Schroder Retorno Total Cuatro B pasó de USD 1 mil a 1,25 M el 07/04/2025).
    - anclado=True: los últimos datos vienen de la planilla CNV validada (patrimonio = cuotapartes x VCP), así que el
      tramo final está en la moneda correcta y se normaliza el resto respecto de él. Si no, la moneda correcta es el
      nivel más bajo (USD).
    Devuelve d corregido y cantidad de tramos corregidos."""
    a = d.aum.values.astype(float)
    if len(a) < 6 or fx is None or moneda != 'USD':   # solo clases USD: el error observado es AUM informado en pesos
        return d, 0
    f = fx.reindex(d.fecha).ffill().bfill().values
    lq = np.log(np.where(a > 0, a, np.nan)); j = np.diff(lq, prepend=np.nan)
    cut = np.where(np.abs(np.abs(j) - np.log(f)) < np.log(1.35))[0]
    if len(cut):                                   # nivel previo y posterior al salto (mediana de hasta 5 obs válidas)
        def lado(c, antes):
            w = a[max(0, c - 5):c] if antes else a[c:c + 5]
            w = w[np.isfinite(w) & (w > 0)]
            return np.median(w) if len(w) else np.nan
        cut = np.array([c for c in cut if min(lado(c, True), lado(c, False)) >= MIN_TRAMO_USD])
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
    # la moneda correcta: el tramo final si está anclado a la CNV; si no, el nivel más bajo (USD)
    target = lvl[seg[-1]] if anclado else lvl.min()
    adj = (lvl[seg] - target)          # cuántas veces hay que dividir (+) o multiplicar (-) por FX
    d = d.copy(); d['aum'] = a / np.power(f, adj)
    return d, len(cut)


def clean_flows(df, moneda, fx=None, anclado=False):
    """Limpieza:
    1) picos transitorios de VCP (salto >40% que revierte en <=5 ruedas) o de AUM (x5 que revierte) -> se elimina la observación
    2) saltos permanentes de VCP >40% (rebase/cambio de unidad) -> 'quiebre' (flag=2): sin rendimiento ni flujo ese día, y
       los rendimientos por horizonte que lo crucen se anulan
    3) saltos diarios > 10% ARS / 5% USD que no son picos -> flag=1: se anula flujo y rendimiento de ese día
    4) AUM x1000 / /1000 -> flag=1"""
    d = df.dropna(subset=['vcp']).copy()
    d = d[d.vcp > 0].reset_index(drop=True)
    d, ncur = fix_currency(d, moneda, fx, anclado)
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
    # flujo entre observaciones con AUM informado: un hueco corto sin patrimonio (<= GAP_MAX ruedas) no corta la serie
    # (el flujo se mide al volver); un hueco largo es una clase vacía: AUM 0 desde el primer día sin patrimonio
    af = _aum_flujos(d.aum)
    v = af.notna()
    av, vv = af[v], d.vcp[v]
    d['flujo'] = np.nan
    d.loc[v, 'flujo'] = av - av.shift(1) * vv / vv.shift(1)
    q = d.aum / d.aum.shift(1)
    bad = (d.rend.abs() > RMAX.get(moneda, 0.05)) | q.between(900, 1100) | q.between(1 / 1100, 1 / 900)
    d['flag'] = np.where(d.fecha.isin(brk_dates), 2, np.where(bad, 1, 0)).astype('int8')
    d.loc[d.flag > 0, ['flujo', 'rend']] = np.nan
    # clase que se activa o se vacía: si el AUM previo es 0, todo el AUM es suscripción (aunque el VCP salte, p. ej. de
    # 1.000 al valor de la clase vieja en un cambio de clase: Lombard Ahorro Plus A -> B el 17/11/2026); si el AUM queda
    # en 0, el rescate es todo el AUM previo
    prev = av.shift(1).reindex(d.index)
    act = v & (prev == 0) & (af > 0)
    vac = v & (prev > 0) & (af == 0)
    d.loc[act, 'flujo'] = af[act]
    d.loc[vac & d.flujo.isna(), 'flujo'] = -prev[vac & d.flujo.isna()]
    if v.any():
        i0 = v.idxmax()                                # primer día con AUM informado
        if d.fecha[i0] > INICIO_HISTORIA and d.aum[i0] > 0 and d.flag[i0] == 0:
            d.loc[i0, 'flujo'] = d.aum[i0]             # clase nueva: su AUM inicial entra como suscripción
    return d


def read_prev(prev_dir):
    """Respaldo si ArgentinaDatos no está disponible: base publicada en la corrida anterior (rama data)."""
    meta = pd.read_parquet(os.path.join(prev_dir, 'clases_meta.parquet'))
    d = pd.concat([pd.read_parquet(f, columns=['fecha', 'clase_id', 'vcp', 'aum'])
                   for f in sorted(glob.glob(os.path.join(prev_dir, 'clases_diario_*.parquet')))], ignore_index=True)
    d['fecha'] = pd.to_datetime(d.fecha); d['clase_id'] = d.clase_id.astype(str)
    g = dict(tuple(d.groupby('clase_id')))
    return {str(m['clase_id']): (m, g[str(m['clase_id'])].drop(columns='clase_id').sort_values('fecha').reset_index(drop=True))
            for m in meta.to_dict('records') if str(m['clase_id']) in g}


def merge_cnv(parts, filas, meta_cnv):
    """CNV es la fuente principal: sus observaciones reemplazan a las de la historia para la misma clase y fecha.
    Clases que sólo están en CNV se agregan con los metadatos de la planilla."""
    if filas is None or filas.empty:
        return parts, 0, 0
    por_cnv = {}
    for m, _ in parts.values():
        if m.get('codigo_cnv') and m.get('fondo_id'):
            por_cnv.setdefault(str(m['codigo_cnv']), str(m['fondo_id']))
    mc = meta_cnv.set_index('clase_id')
    reemplazos = nuevas = 0
    for k, g in filas.groupby('clase_id'):
        g = g.drop(columns='clase_id')
        if k in parts:
            m, df = parts[k]
            prev = df.set_index('fecha')
            comun = prev.index.intersection(g.fecha)
            reemplazos += int((~np.isclose(prev.loc[comun, 'vcp'].values, g.set_index('fecha').loc[comun, 'vcp'].values)).sum())
            df = pd.concat([df[~df.fecha.isin(g.fecha)], g], ignore_index=True).sort_values('fecha').reset_index(drop=True)
            m = {**m, 'fecha_ultima': df.fecha.max(), 'n_obs': len(df)}
        else:
            r = mc.loc[k]
            cod = r.get('codigo_cnv')
            m = dict(slug=f'cnv-{k}', clase_id=k, fondo_id=por_cnv.get(str(cod), f'cnv-{cod}'), clase=r['nombre'],
                     administradora=r.get('gerente'), depositaria=r.get('depositaria'), tipo_renta=r.get('tipo_renta'),
                     region=r.get('region'), horizonte=r.get('horizonte'), benchmark=None, moneda_raw=r['moneda'],
                     plazo_liq=r.get('plazo_liq'), inversion_min=r.get('inversion_min'), codigo_cnv=cod,
                     hon_gerente=r.get('hon_gerente'), hon_depositaria=r.get('hon_depositaria'), gastos=r.get('gastos'),
                     com_ingreso=r.get('com_ingreso'), com_egreso=r.get('com_egreso'), fecha_ultima=g.fecha.max(),
                     n_obs=len(g), moneda=r['moneda'])
            df = g.sort_values('fecha').reset_index(drop=True)
            nuevas += 1
        parts[k] = (m, df)
    return parts, reemplazos, nuevas


def build(src_fondos, out_dir, fx_csv=None, cnv_filas=None, cnv_meta=None, prev_dir=None):
    """Historia: ArgentinaDatos (o, si no está, la base anterior publicada) + CNV como fuente principal reciente."""
    os.makedirs(out_dir, exist_ok=True)
    fx = pd.read_csv(fx_csv, index_col=0, parse_dates=True).a3500 if fx_csv and os.path.exists(fx_csv) else None
    metas, parts = [], {}
    dirs = sorted(glob.glob(os.path.join(src_fondos, '*'))) if src_fondos and os.path.isdir(src_fondos) else []
    for i, dp in enumerate(dirs):
        m, df = read_class(dp)
        if m is None:
            continue
        m['moneda'] = MONEDA.get(m['moneda_raw'], 'USD' if 'lar' in str(m['moneda_raw']) else 'ARS')
        k = m['clase_id']
        if k in parts and (parts[k][1].fecha.max(), len(parts[k][1])) >= (df.fecha.max(), len(df)):
            continue
        parts[k] = (m, df)
    historia = 'ArgentinaDatos'
    if len(parts) < 1000:
        if not (prev_dir and os.path.exists(os.path.join(prev_dir, 'clases_meta.parquet'))):
            raise RuntimeError('Sin historia: ArgentinaDatos no disponible y no hay base anterior')
        parts = read_prev(prev_dir); historia = 'base anterior publicada (ArgentinaDatos no disponible)'
    parts, reemp, nuevas = merge_cnv(parts, cnv_filas, cnv_meta)
    print(f'historia: {historia} | CNV: {0 if cnv_filas is None else len(cnv_filas)} obs, {reemp} VCP distintos reemplazados, {nuevas} clases nuevas')
    allrows = []
    cnv_ids = set() if cnv_filas is None else set(cnv_filas.clase_id.astype(str))
    for k, (m, df) in parts.items():
        d = clean_flows(df, m['moneda'], fx, anclado=str(k) in cnv_ids)
        d['clase_id'] = k
        allrows.append(d)
        metas.append(m)
    base = pd.concat(allrows, ignore_index=True)
    base['clase_id'] = base.clase_id.astype('string')
    meta = pd.DataFrame(metas)
    for y, g in base.groupby(base.fecha.dt.year):
        g.drop(columns=[]).to_parquet(os.path.join(out_dir, f'clases_diario_{y}.parquet'), index=False, compression='zstd')
    meta.to_parquet(os.path.join(out_dir, 'clases_meta.parquet'), index=False)
    open(os.path.join(out_dir, 'fuente_historia.txt'), 'w').write(historia)
    print(f'clases={len(meta)} filas={len(base):,} desde={base.fecha.min().date()} hasta={base.fecha.max().date()} '
          f'flag1={int((base.flag == 1).sum())} quiebres={int((base.flag == 2).sum())}')
    return base, meta


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
