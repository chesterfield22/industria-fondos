"""Carteras semanales de los FCI, tomadas de la ficha de cada fondo en la CNV
(https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/DetallesFCI/{id}, "Composición Semanal Cartera").

Uso:  python pipeline/carteras.py <dir_publicado_anterior> <dir_salida> [<dir_base_data>] [--forzar]

Capas (no se pisan los datos crudos):
  historico/AAAA-MM-DD.parquet  CRUDO: una fila por renglón de la cartera tal como lo informa la CNV (rubros, especies,
                                total), particionado por fecha de cartera. Se agrega o reemplaza por fondo y fecha.
  derivado/ultimas.parquet      TRANSFORMADO: última cartera de cada fondo + tipo de activo, emisor y moneda (estimados),
                                vínculo con la planilla diaria (fondo_id) y comparación con la cartera anterior.
  indice.csv, f/<id>.csv        PARA EL SHEET: índice de fondos y un CSV por fondo con ficha, métricas y tenencias.
  estado.json                   control de corridas (fecha centinela, fondos pendientes).

Eficiencia: cada corrida consulta primero solo la ficha centinela (IOL Cash Management). Si su "Cartera al" no cambió
y no hay fondos pendientes, termina sin descargar ni publicar (escribe SIN_CAMBIOS en la salida).
"""
import concurrent.futures as cf
import csv
import datetime as dt
import glob
import html
import json
import os
import re
import shutil
import sys
import time
import unicodedata
import urllib.error
import urllib.request

import numpy as np
import pandas as pd

BASE = 'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept-Language': 'es-AR,es;q=0.9'}
CENTINELA = '84033'          # IOL Cash Management
HILOS = 4                    # consultas simultáneas a la CNV (moderado a propósito)
FRESCA_DIAS = 14             # una cartera más vieja que (fecha más reciente del sistema - 14 días) se marca desactualizada
PENDIENTE_DIAS = 21          # fondos atrasados hasta 21 días se reintentan (después de una corrida completa)
MAX_REINTENTOS = 3
PRESUPUESTO_MIN = float(os.environ.get('CARTERAS_PRESUPUESTO_MIN', 25))  # minutos de descarga por corrida (el resto sigue en otra)
INACTIVO_DIAS = 120          # sin cartera o con cartera > 120 días detrás de la más reciente: se saltea en los barridos
REVISAR_INACTIVOS_DIAS = 28  # ... salvo un barrido completo cada 4 semanas (por si vuelven a informar)
N_SIMILARES = 8


def hoy_art():
    return (dt.datetime.utcnow() - dt.timedelta(hours=3)).date()


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------------------------------------------------ descarga
def get(url, intentos=4, timeout=60):
    err = None
    for k in range(intentos):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if r.status == 200:
                    return r.read().decode('utf-8', 'ignore')
                err = f'HTTP {r.status}'
        except urllib.error.HTTPError as e:
            err = f'HTTP {e.code}'
            if e.code in (400, 403, 404):
                break
        except Exception as e:  # timeouts, cortes
            err = f'{type(e).__name__}: {str(e)[:80]}'
        time.sleep((2, 6, 15, 30)[min(k, 3)])
    raise RuntimeError(f'{url}: {err}')


def txt(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def num(s):
    s = (s or '').strip().replace(' ', '')
    if not s:
        return None
    neg = s.startswith('(') and s.endswith(')')
    s = s.strip('()')
    try:
        v = float(s.replace('.', '').replace(',', '.'))
    except ValueError:
        return None
    return -v if neg else v


def listar_fondos():
    """(ficha_id, nombre) de todos los fondos del buscador de la CNV (select Lfc)."""
    h = get(BASE)
    m = re.search(r'<select[^>]*(?:name|id)="Lfc"[^>]*>(.*?)</select>', h, re.S | re.I)
    if not m:
        raise RuntimeError('no se encontró el listado de fondos (select Lfc) en la página de la CNV')
    out = [(v, txt(t)) for v, t in re.findall(r'<option[^>]*value="(\d+)"[^>]*>(.*?)</option>', m.group(1), re.S | re.I)]
    if len(out) < 500:
        raise RuntimeError(f'listado de fondos sospechosamente corto ({len(out)})')
    return out


CAB = {'Fondo': 'fondo', 'Cartera al (*)': 'cartera_al', 'Gerente': 'gerente', 'Depositaria': 'depositaria',
       'Clasificación': 'clasificacion', 'Moneda': 'moneda_fondo', 'Región': 'region', 'Horizonte': 'horizonte'}


def parse_ficha(h):
    """Cabecera y renglones de la 'Composición Semanal Cartera'. None si la ficha no tiene cartera."""
    i = h.find('Composición Semanal Cartera')
    if i < 0:
        i = h.find('Composici&#243;n Semanal Cartera')
    if i < 0:
        return None
    sec = h[i:]
    j = sec.find('VOLVER')
    sec = sec[: j if j > 0 else 400_000]
    cab, filas, en_tabla = {}, [], False
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', sec, re.S | re.I):
        c = [txt(x) for x in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
        if not c or not any(c):
            continue
        if not en_tabla:
            if c[0] in CAB and len(c) >= 2:
                cab[CAB[c[0]]] = c[1]
            elif c[0].startswith('Detalle'):
                en_tabla = True
            continue
        filas.append((c + ['', '', ''])[:3])
    try:
        cab['cartera_al'] = dt.datetime.strptime(cab.get('cartera_al', ''), '%d/%m/%y').date()
    except ValueError:
        return None
    rows, pn, rubro = [], None, None
    for k, (a, p1, p2) in enumerate(filas):
        v1, v2 = num(p1), num(p2)
        if a.startswith('(') and a.endswith(')') and v1 is not None:      # (Peso Argentina)  patrimonio
            pn, cab['moneda_pn'] = v1, a.strip('()')
            continue
        if not a:                                                             # total 100,00
            rows.append(dict(orden=k, nivel='total', rubro=None, especie=None, pct=v2))
            continue
        if v1 is not None:                                                    # especie (% en 1ra columna)
            rows.append(dict(orden=k, nivel='especie', rubro=rubro, especie=a, pct=v1))
        else:                                                                 # rubro (subtotal en 2da, puede venir vacío)
            rubro = a
            rows.append(dict(orden=k, nivel='rubro', rubro=a, especie=None, pct=v2))
    cab['pn'] = pn
    return cab, rows


def bajar_ficha(fid):
    try:
        p = parse_ficha(get(f'{BASE}/DetallesFCI/{fid}'))
        return fid, ('ok', p) if p else ('sin_cartera', None)
    except Exception as e:
        return fid, ('error', str(e)[:200])


def bajar(ids, presupuesto_s=None):
    """Baja fichas en tandas hasta agotar el presupuesto de tiempo. Devuelve (resultados, ids que quedan)."""
    res, t0, ids = {}, time.time(), list(ids)
    tanda = HILOS * 8
    with cf.ThreadPoolExecutor(HILOS) as ex:
        while ids and (presupuesto_s is None or not res or time.time() - t0 < presupuesto_s):   # al menos una tanda
            lote, ids = ids[:tanda], ids[tanda:]
            for fid, r in ex.map(bajar_ficha, lote):
                res[fid] = r
            if len(res) % 160 < tanda:
                log(f'  {len(res)} fichas ({time.time() - t0:.0f}s, {(time.time() - t0) / len(res):.1f}s/ficha), quedan {len(ids)}')
    c = pd.Series([r[0] for r in res.values()]).value_counts().to_dict() if res else {}
    log(f'  descargadas {len(res)} fichas en {time.time() - t0:.0f}s: {c}; quedan {len(ids)}')
    return res, ids


# ------------------------------------------------------------------------------------------------------------ histórico
COLS_CAB = ['fondo', 'gerente', 'depositaria', 'clasificacion', 'moneda_fondo', 'region', 'horizonte', 'pn', 'moneda_pn']


def a_filas(res, relevado):
    out = []
    for fid, (st, p) in res.items():
        if st != 'ok':
            continue
        cab, rows = p
        for r in rows:
            out.append(dict(fecha_cartera=cab['cartera_al'], ficha_id=fid, **{k: cab.get(k) for k in COLS_CAB}, **r,
                            relevado=relevado))
    df = pd.DataFrame(out)
    if len(df):
        df['fecha_cartera'] = pd.to_datetime(df.fecha_cartera)
        df['pct'] = df.pct.astype(float)
        df['pn'] = df.pn.astype(float)
        df['orden'] = df.orden.astype('int32')
    return df


def guardar_historico(nuevo, hdir):
    """Agrega/reemplaza por (fecha_cartera, ficha_id). Un archivo por fecha de cartera."""
    os.makedirs(hdir, exist_ok=True)
    for f, g in nuevo.groupby('fecha_cartera'):
        p = os.path.join(hdir, f'{f.date()}.parquet')
        if os.path.exists(p):
            old = pd.read_parquet(p)
            g = pd.concat([old[~old.ficha_id.isin(g.ficha_id.unique())], g], ignore_index=True)
        g.sort_values(['ficha_id', 'orden']).to_parquet(p, index=False, compression='zstd')


def leer_historico(hdir):
    fs = sorted(glob.glob(os.path.join(hdir, '*.parquet')))
    return pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True) if fs else pd.DataFrame()


# ------------------------------------------------------------------------------------------------------------ derivados
def norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', s).strip()


REGLAS_TIPO = [
    (r'^caucion', 'Caución'), (r'^pase', 'Pase'), (r'^(pzo|plazo) fi', 'Plazo fijo'),
    (r'^pagare', 'Pagaré'), (r'^cheque', 'Cheque'), (r'^(fce|factura)', 'FCE'),
    (r'^(vcp|valor(es)? (representativo|de corto))', 'VCP'), (r'^on ', 'ON'),
    (r'bopreal|^lefi|^nobac|^lebac|\bbcra\b', 'BCRA'),
    (r'eeuu|ee\.?uu|\bu\.?s\.? treasury|t-?bills?\b|united states|tesoro (de )?estados unidos', 'Soberano exterior'),
    (r'^[a-z]{2,3} (rep|republica|bono global|global|tesoro|gobierno|govt)\b', 'Soberano exterior'),
    (r'^([a-z]{2,3} |bonos? )?(chile|brasil|paraguay|uruguay|peru|mexico|colombia|panama|ecuador)\b', 'Soberano exterior'),
    (r'\betf\b|ishares|^spy |qqq|powershares|spdr|vanguard', 'ETF'),
    (r'^bonos? (disc|par|cuasipar|bocon|consolidacion)\b', 'Soberano'),
    (r'^bonos? .*\bprov(\.|incia)? de\b', 'Subsoberano'),
    (r'^bc ', 'Corporativo exterior'),
    (r'^(lecap|boncap|lecer|boncer|lede|lelink|letra tamar|letras? (del )?tesoro|bonos? (del tesoro|boncer|cer|capitalizable|'
     r'dual|tamar|bonar|global)|bonos? rep|bonar|global|bontam|bote|bonte|tx\d|tz)', 'Soberano'),
    (r'^(bono|letra|titulo|tit)s? (de (la )?)?(prov|muni|ciudad|caba|pcia)|^(bono|letra)s? (buenos aires|cordoba|santa fe|'
     r'neuquen|mendoza|chubut|entre rios|rio negro|salta|jujuy|tierra del fuego|chaco|la rioja|catamarca|ciudad)', 'Subsoberano'),
    (r'^ff ', 'Fideicomiso financiero'), (r'^fci ', 'FCI'), (r'^cedear', 'Cedear'),
    (r'^(futuro|fut |opcion|opc |call |put |ndf|rofex)', 'Derivado'),
]
REGLAS_RUBRO = [('participaciones accionarias', 'Acción'), ('titulos de deuda', 'Otro título de deuda'),
                ('certificados de participacion', 'Otro certificado de participación'),
                ('operaciones a plazo', 'Otra operación a plazo')]


def tipo_activo(esp, rubro):
    e, r = norm(esp), norm(rubro)
    for rx, t in REGLAS_TIPO:
        if re.search(rx, e):
            return t
    if 'certificados de participacion' in r and re.search(r'(^| - )clase [a-z0-9]+$| - clase ', e):
        return 'FCI'
    for k, t in REGLAS_RUBRO:
        if k in r:
            return t
    return 'Otro'


MONEDAS = {'peso argentina': 'ARS', 'dolar estadounidense': 'USD', 'euro': 'EUR', 'real': 'BRL', 'peso chileno': 'CLP',
           'peso uruguayo': 'UYU', 'libra esterlina': 'GBP', 'yen': 'JPY'}


def moneda_activo(esp, rubro, tipo):
    r = norm(rubro)
    m = re.search(r' en (.+)$', r)
    if m and m.group(1) in MONEDAS:
        return MONEDAS[m.group(1)]
    e = (esp or '')
    if re.search(r'U\$S|US\$|\bUSD\b|D[oó]lar', e):
        return 'USD'
    if re.search(r'(^|\s)\$(\s|$)', e) or tipo in ('Cedear', 'Pagaré', 'Cheque'):
        return 'ARS' if not re.search(r'#UMV', e) else 'USD'
    if tipo == 'Acción' and 'argentina' in r:
        return 'ARS'
    return ''


PAISES = {'CL': 'Chile', 'BR': 'Brasil', 'UY': 'Uruguay', 'PE': 'Perú', 'MX': 'México', 'CO': 'Colombia', 'PY': 'Paraguay', 'PAR': 'Paraguay',
          'US': 'EE.UU.', 'PA': 'Panamá', 'EC': 'Ecuador', 'BO': 'Bolivia'}


def _limpiar_emisor(s):
    s = re.sub(r'\((?:reg ?s|144a|ley .*?)\)', ' ', s, flags=re.I)
    s = re.sub(r'\s+(?:Clase|Cl|Serie|Ser)\.?\s*[A-Z0-9][\w\-]*\b.*$', '', s)
    s = re.sub(r'\s+(?:C|S|CL|SE)\s?\d[\w\-]*\b.*$', '', s)
    s = re.sub(r'\s+\d.*$', '', s)
    s = re.sub(r'(\s+\S*\d\S*)+$', '', s)
    s = re.sub(r'^Bco\.? ', 'Banco ', s)
    s = re.sub(r'^Banco Prov$', 'Banco Provincia', s)
    s = re.sub(r'\s+(S\.?A\.?U?\.?|S\.?R\.?L\.?)$', '', s.strip())
    return s.strip(' .-')


def emisor(esp, tipo):
    e = esp or ''
    if tipo == 'ON':
        return _limpiar_emisor(e[3:])
    if tipo in ('Soberano',):
        return 'Tesoro Nacional'
    if tipo == 'BCRA':
        return 'BCRA'
    if tipo == 'Soberano exterior':
        if re.search(r'eeuu|ee\.?uu|treasury|t-?bill|united states|estados unidos', e, re.I):
            return 'Tesoro EE.UU.'
        for nom in PAISES.values():
            if norm(nom) in norm(e):
                return nom + ' (soberano)'
        cod = e.split()[0].upper()
        return PAISES.get(cod, cod) + ' (soberano)'
    if tipo == 'Corporativo exterior':
        return _limpiar_emisor(e[3:])
    if tipo == 'Subsoberano' and re.search(r'prov(\.|incia)? de', e, re.I) and not re.match(r'(Bono|Letra|Titulo)s?\s+Prov', e, re.I):
        return 'Prov. ' + _limpiar_emisor(re.split(r'prov(?:\.|incia)? de\s+', e, flags=re.I)[-1])
    if tipo == 'Subsoberano':
        s = re.sub(r'^(Bono|Letra|Titulo|Tit)s?\s+', '', e, flags=re.I)
        pre = 'Muni. ' if re.match(r'muni', s, re.I) else 'Prov. '
        s = re.sub(r'^(Prov(incia)?|Pcia|Muni(cipalidad)?)\.?\s+(de\s+(la\s+)?)?', '', s, flags=re.I)
        s = re.sub(r'^(Bs\.? ?As\.?|Bsas)\b', 'Buenos Aires', _limpiar_emisor(s), flags=re.I)
        return pre + s
    if tipo == 'Plazo fijo':
        return _limpiar_emisor(re.sub(r'^(Pzo|Plazo)\s+Fi(jo)?\.?\s*(\$|U\$S|US\$|USD)?\s*', '', e, flags=re.I))
    if tipo in ('Caución', 'Pase'):
        return tipo + ' ' + (e.split()[-1] if e.split() else '')
    if tipo in ('Pagaré', 'Cheque', 'FCE', 'Derivado'):
        return ''                                  # la CNV no informa el librador / contraparte
    if tipo == 'FCI':
        return re.split(r'\s+-\s+|\s+Clase\s', e[4:])[0].strip()
    if tipo == 'Fideicomiso financiero':
        return _limpiar_emisor(e[3:])
    if tipo == 'ETF':
        return e.strip()
    if tipo == 'Cedear':
        return re.sub(r'^Cedear\s+', '', e, flags=re.I).strip()
    if tipo == 'Acción':
        return _limpiar_emisor(e)
    return _limpiar_emisor(e)


def derivar(ult):
    esp = ult.nivel == 'especie'
    ult.loc[esp, 'tipo'] = [tipo_activo(e, r) for e, r in zip(ult.especie[esp], ult.rubro[esp])]
    ult.loc[esp, 'emisor'] = [emisor(e, t) for e, t in zip(ult.especie[esp], ult.tipo[esp])]
    ult.loc[esp, 'moneda'] = [moneda_activo(e, r, t) for e, r, t in zip(ult.especie[esp], ult.rubro[esp], ult.tipo[esp])]
    return ult


# ------------------------------------------------------------------------------------------------------------ vínculo
def nfondo(s):
    s = norm(s)
    s = re.sub(r'\b(f\.?\s?c\.?\s?i\.?\s?a?\.?|fondo comun de inversion( abierto)?( para)?|ex [^-]*$)\b', ' ', s)
    s = re.sub(r'\bdolar(es)?\b', 'dolar', s)
    return re.sub(r'[^a-z0-9]+', ' ', s).strip()


def vincular(ult, base_dir):
    """ficha CNV -> fondo_id de la planilla diaria (nombre + gerente) y control de patrimonio vs AUM de la planilla."""
    cab = ult.drop_duplicates('ficha_id')[['ficha_id', 'fondo', 'gerente', 'fecha_cartera', 'pn', 'moneda_fondo']].copy()
    vacio = pd.DataFrame({'ficha_id': cab.ficha_id, 'fondo_id': None, 'subtipo': None, 'gerente_corto': None,
                          'aum_planilla': np.nan, 'aum_moneda': None})
    try:
        m = pd.read_parquet(os.path.join(base_dir, 'maestro.parquet'))
    except Exception as e:
        log('  sin maestro para vincular:', e)
        return vacio
    m['k'] = m.fondo.map(nfondo)
    m['g'] = m.administradora.map(norm)
    cab['k'] = cab.fondo.map(nfondo)
    cab['g'] = cab.gerente.map(norm)
    fm = m.drop_duplicates(['fondo_id', 'k'])
    # 1) nombre + gerente, 2) solo nombre si es único, 3) parecido (>=0.92) dentro del mismo gerente
    a = cab.merge(fm[['k', 'g', 'fondo_id']], on=['k', 'g'], how='left')
    a = a.drop_duplicates('ficha_id')
    falt = a.fondo_id.isna()
    unico = fm.groupby('k').fondo_id.nunique()
    solo = fm[fm.k.isin(unico[unico == 1].index)].drop_duplicates('k').set_index('k').fondo_id
    a.loc[falt, 'fondo_id'] = a.loc[falt, 'k'].map(solo)
    import difflib
    for i in a.index[a.fondo_id.isna()]:
        cands = fm[fm.g == a.at[i, 'g']]
        if len(cands):
            r = difflib.get_close_matches(a.at[i, 'k'], cands.k.tolist(), n=1, cutoff=0.92)
            if r:
                a.at[i, 'fondo_id'] = cands[cands.k == r[0]].fondo_id.iloc[0]
    # dato de la planilla: subtipo, gerente corto (de la clase de referencia)
    ref = m.sort_values('aum_ref', ascending=False).drop_duplicates('fondo_id').set_index('fondo_id')
    a['subtipo'] = a.fondo_id.map(ref.subtipo)
    a['gerente_corto'] = a.fondo_id.map(ref.gerente)
    # AUM de la planilla el mismo día, en la moneda del fondo (clases USD/ARS convertidas al A3500 del día)
    a['aum_planilla'] = np.nan
    try:
        fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), parse_dates=['fecha']).set_index('fecha').a3500.sort_index()
        fm_ = a.fecha_cartera.max()
        anios = sorted({f.year for f in a.fecha_cartera.dropna() if f >= fm_ - pd.Timedelta(days=60)})
        cd = pd.concat([pd.read_parquet(os.path.join(base_dir, f'clases_diario_{y}.parquet'), columns=['fecha', 'clase_id', 'aum'])
                        for y in anios if os.path.exists(os.path.join(base_dir, f'clases_diario_{y}.parquet'))])
        cd = cd[cd.aum.notna() & cd.fecha.isin(a.fecha_cartera.unique())]
        cm = m.drop_duplicates('clase_id').set_index('clase_id')
        cd['fondo_id'] = cd.clase_id.map(cm.fondo_id)
        cd['mon'] = cd.clase_id.map(cm.moneda)
        cd['tc'] = cd.fecha.map(fx.reindex(fx.index.union(cd.fecha.unique())).ffill())
        cd['aum_ars'] = np.where(cd.mon == 'USD', cd.aum * cd.tc, cd.aum)
        tot = cd.groupby(['fondo_id', 'fecha']).aum_ars.sum()
        key = list(zip(a.fondo_id, a.fecha_cartera))
        ars = pd.Series([tot.get(k, np.nan) for k in key], index=a.index)
        tc = a.fecha_cartera.map(fx.reindex(fx.index.union(a.fecha_cartera.dropna().unique())).ffill())
        usd = a.moneda_fondo.map(norm).str.contains('dolar', na=False)
        a['aum_planilla'] = np.where(usd, ars / tc, ars)
    except Exception as e:
        log('  no se pudo calcular el AUM de la planilla para el control:', type(e).__name__, e)
    a.loc[a.aum_planilla <= 0, 'aum_planilla'] = np.nan
    log(f'  vínculo con la planilla diaria: {a.fondo_id.notna().sum()}/{len(a)} fichas; '
        f'con AUM del mismo día {a.aum_planilla.notna().sum()}')
    return a[['ficha_id', 'fondo_id', 'subtipo', 'gerente_corto', 'aum_planilla']]


# ------------------------------------------------------------------------------------------------------------ métricas
def fmt(v):
    """Números sin notación científica: montos grandes sin decimales, el resto con 4 decimales."""
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ''
    if isinstance(v, (bool, np.bool_)):
        return str(v)
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        v = float(v)
        return f'{v:.0f}' if abs(v) >= 1e6 else f'{round(v, 4):.4f}'.rstrip('0').rstrip('.')
    return str(v)


def hhi(w):
    w = np.asarray([x for x in w if x and x > 0], float)
    return float((w ** 2).sum()) if len(w) else np.nan


def similares(esp_fresca):
    """Fondos con cartera más parecida: similitud coseno sobre pesos por especie (normalizados a 100) y superposición
    = suma de mínimos de pesos (cuánto de la cartera es común)."""
    e = esp_fresca[esp_fresca.pct > 0]
    tot = e.groupby('ficha_id').pct.transform('sum')
    e = e.assign(w=e.pct / tot * 100)
    e = e.groupby(['ficha_id', 'especie'], as_index=False).w.sum()
    fids = sorted(e.ficha_id.unique())
    ix_f = {f: i for i, f in enumerate(fids)}
    esps = e.especie.unique()
    ix_e = {s: i for i, s in enumerate(esps)}
    M = np.zeros((len(fids), len(esps)), np.float32)
    M[e.ficha_id.map(ix_f).values, e.especie.map(ix_e).values] = e.w.values
    nrm = np.linalg.norm(M, axis=1, keepdims=True)
    nrm[nrm == 0] = 1
    U = M / nrm
    out = {}
    K = 50                                                  # candidatos por coseno; se ordenan por superposición
    for a0 in range(0, len(fids), 256):                     # por bloques para acotar memoria
        S = U[a0:a0 + 256] @ U.T
        for k in range(S.shape[0]):
            i = a0 + k
            S[k, i] = -1
            kk = min(K, len(fids) - 1)
            top = np.argpartition(-S[k], kk)[:kk]
            filas = []
            for j in top:
                if S[k, j] <= 0:
                    continue
                both = (M[i] > 0) & (M[j] > 0)
                filas.append((fids[j], float(S[k, j]), float(np.minimum(M[i], M[j]).sum()), int(both.sum())))
            out[fids[i]] = sorted(filas, key=lambda x: (-x[2], -x[1]))[:N_SIMILARES]
    return out


def construir(hist, vin, hoy, outdir, listado_n):
    """Última cartera por fondo + derivados + métricas -> derivado/ultimas.parquet, indice.csv, f/<id>.csv"""
    hist = hist.copy()
    ultf = hist.groupby('ficha_id').fecha_cartera.max()
    hist['es_ult'] = hist.fecha_cartera == hist.groupby('ficha_id').fecha_cartera.transform('max')
    ult = derivar(hist[hist.es_ult].drop(columns='es_ult').copy())
    prev = hist[~hist.es_ult & (hist.nivel == 'especie')]
    prevf = prev.groupby('ficha_id').fecha_cartera.max().to_dict()
    prev = prev[prev.fecha_cartera == prev.groupby('ficha_id').fecha_cartera.transform('max')]
    ult = ult.merge(vin, on='ficha_id', how='left')
    fmax = ult.fecha_cartera.max()
    cab = ult.drop_duplicates('ficha_id').set_index('ficha_id')
    fresca = cab.index[cab.fecha_cartera >= fmax - pd.Timedelta(days=FRESCA_DIAS)]
    esp = ult[ult.nivel == 'especie']
    esp_fresca = esp[esp.ficha_id.isin(fresca)]
    n_fci = esp_fresca.groupby('especie').ficha_id.nunique()
    sim = similares(esp_fresca)
    # cambios vs cartera anterior
    pa = prev.groupby(['ficha_id', 'especie']).pct.sum()
    ult['pct_ant'] = [pa.get((f, s), np.nan) if n == 'especie' else np.nan for f, s, n in zip(ult.ficha_id, ult.especie, ult.nivel)]
    ult['n_fci'] = ult.especie.map(n_fci)
    ult['fecha_anterior'] = pd.to_datetime(ult.ficha_id.map(prevf))
    cab = ult.drop_duplicates('ficha_id').set_index('ficha_id')

    os.makedirs(os.path.join(outdir, 'derivado'), exist_ok=True)
    ult.to_parquet(os.path.join(outdir, 'derivado', 'ultimas.parquet'), index=False, compression='zstd')
    fdir = os.path.join(outdir, 'f')
    shutil.rmtree(fdir, ignore_errors=True)
    os.makedirs(fdir)

    # etiquetas únicas para el selector
    lab = cab.fondo.fillna('').str.strip()
    dup = lab.duplicated(keep=False)
    lab = lab.where(~dup, lab + ' · ' + cab.gerente_corto.fillna(cab.gerente.str.split().str[0]).fillna(''))
    dup = lab.duplicated(keep=False)
    lab = lab.where(~dup, lab + ' (' + lab.index + ')')

    idx = []
    for fid, c in cab.iterrows():
        g = ult[ult.ficha_id == fid]
        e = g[g.nivel == 'especie'].sort_values('pct', ascending=False)
        r = g[g.nivel == 'rubro']
        pas = r[r.rubro.map(norm) == 'pasivos'].pct
        pasivos = float(pas.iloc[0]) if len(pas) and pd.notna(pas.iloc[0]) else np.nan
        rub_act = r[(r.rubro.map(norm) != 'pasivos') & r.pct.notna()].pct
        activos = float(rub_act.sum()) if len(rub_act) else float(e.pct.sum())
        disp = r[r.rubro.map(norm) == 'disponibilidades'].pct
        disp = float(disp.iloc[0]) if len(disp) and pd.notna(disp.iloc[0]) else 0.0
        liq = disp + float(e[e.tipo.isin(['Caución', 'Pase'])].pct.sum())
        em = e[e.emisor.fillna('') != ''].groupby('emisor').pct.sum().sort_values(ascending=False)
        sin_em = float(e[e.emisor.fillna('') == ''].pct.sum())
        dias = (pd.Timestamp(hoy) - c.fecha_cartera).days
        atraso = (fmax - c.fecha_cartera).days
        avisos = []
        if atraso > FRESCA_DIAS:
            avisos.append(f'Cartera desactualizada: la CNV muestra la del {c.fecha_cartera:%d/%m/%Y}, {atraso} días anterior a '
                          f'la más reciente del sistema ({fmax:%d/%m/%Y}). Puede ser un fondo inactivo o en liquidación.')
        if activos < 50 or activos > 200 or (pd.notna(pasivos) and pasivos > 50):
            avisos.append(f'Datos atípicos: activos {activos:.2f}% del patrimonio'
                          + (f', pasivos informados {pasivos:.2f}%' if pd.notna(pasivos) else '') + '. Se muestra como lo informa la CNV.')
        if len(e) == 0:
            avisos.append('Sin especies informadas (solo rubros).')
        if pd.isna(c.fondo_id):
            avisos.append('No vinculado a la planilla diaria (nombre distinto): sin subtipo ni control de patrimonio.')
        dif = (c.pn / c.aum_planilla - 1) if pd.notna(c.aum_planilla) and c.pn else np.nan
        if pd.notna(dif) and abs(dif) > 0.05:
            avisos.append(f'El patrimonio de la CNV difiere {dif:+.1%} del AUM de la planilla diaria del mismo día.')
        estado = ('desactualizada' if atraso > FRESCA_DIAS else 'atipica' if any(a.startswith('Datos atípicos') for a in avisos)
                  else 'ok')
        meta = {
            'fondo': c.fondo, 'gerente': c.gerente, 'depositaria': c.depositaria, 'clasificacion': c.clasificacion,
            'subtipo': c.subtipo or '', 'moneda_fondo': c.moneda_fondo, 'region': c.region, 'horizonte': c.horizonte,
            'cartera_al': c.fecha_cartera.date().isoformat(), 'dias': dias,
            'fecha_anterior': c.fecha_anterior.date().isoformat() if pd.notna(c.fecha_anterior) else '',
            'pn': c.pn, 'aum_planilla': c.aum_planilla, 'dif_pn_aum': dif, 'relevado': str(c.relevado)[:16],
            'estado': estado, 'aviso': '  ·  '.join(avisos), 'url': f'{BASE}/DetallesFCI/{fid}',
            'n_especies': len(e), 'activos_pn': activos, 'pasivos_cnv': pasivos, 'liquidez': liq,
            'top1': float(e.pct.head(1).sum()), 'top5': float(e.pct.head(5).sum()), 'top10': float(e.pct.head(10).sum()),
            'hhi_especies': hhi(e.pct), 'n_emisores': len(em), 'emisor_top': em.index[0] if len(em) else '',
            'emisor_top_pct': float(em.iloc[0]) if len(em) else np.nan, 'hhi_emisores': hhi(em.values),
            'sin_emisor_pct': sin_em,
        }
        rows = [('m', k, v) for k, v in meta.items()]
        for t, s in e.groupby('tipo').pct.agg(['sum', 'count']).sort_values('sum', ascending=False).iterrows():
            rows.append(('t', t, s['sum'], int(s['count'])))
        for mo, s in e.assign(moneda=e.moneda.replace('', 'sin dato')).groupby('moneda').pct.sum().sort_values(ascending=False).items():
            rows.append(('c', mo, s))
        for _, x in r.sort_values('orden').iterrows():
            rows.append(('r', x.rubro, x.pct))
        rows.append(('r', 'Activos − 100% (financiamiento / pasivos netos)', activos - 100))
        for _, x in e.iterrows():
            monto = x.pct / 100 * c.pn if c.pn else np.nan
            delta = (x.pct - x.pct_ant) if pd.notna(x.pct_ant) else (x.pct if pd.notna(c.fecha_anterior) else np.nan)
            rows.append(('e', x.especie, x.tipo, x.emisor, x.moneda, x.pct, monto, x.pct_ant, delta, x.rubro,
                         x.n_fci if pd.notna(x.n_fci) else np.nan))
        if pd.notna(c.fecha_anterior):
            ant = prev[prev.ficha_id == fid]
            for _, x in ant[~ant.especie.isin(e.especie)].sort_values('pct', ascending=False).iterrows():
                rows.append(('b', x.especie, x.pct, tipo_activo(x.especie, x.rubro)))
        for j, cos, sup, com in sim.get(fid, []):
            cj = cab.loc[j]
            rows.append(('p', lab.get(j, cj.fondo), cj.gerente_corto if pd.notna(cj.gerente_corto) else cj.gerente,
                         cj.subtipo if pd.notna(cj.subtipo) else '', sup, cos, com))
        ancho = max(len(x) for x in rows)
        with open(os.path.join(fdir, f'{fid}.csv'), 'w', newline='', encoding='utf-8') as fh:
            w = csv.writer(fh)
            w.writerow(['s', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j'][:ancho])
            for x in rows:
                w.writerow([fmt(v) for v in x] + [''] * (ancho - len(x)))
        idx.append(dict(etiqueta=lab[fid], ficha_id=fid, fondo=c.fondo, gerente=c.gerente_corto if pd.notna(c.gerente_corto) else c.gerente,
                        subtipo=c.subtipo or '', clasificacion_cnv=c.clasificacion, moneda=c.moneda_fondo,
                        cartera_al=c.fecha_cartera.date(), dias=dias, pn=c.pn, n_especies=len(e), estado=estado,
                        fondo_id=c.fondo_id if pd.notna(c.fondo_id) else ''))
    ind = pd.DataFrame(idx).sort_values('etiqueta', key=lambda s: s.map(norm))
    ind['pn'] = ind.pn.round(2)
    ind.to_csv(os.path.join(outdir, 'indice.csv'), index=False, float_format='%.2f')
    resumen = dict(fecha_max=str(fmax.date()), fondos=len(ind), frescos=int((ind.estado == 'ok').sum()),
                   desactualizados=int((ind.estado == 'desactualizada').sum()), atipicos=int((ind.estado == 'atipica').sum()),
                   vinculados=int((ind.fondo_id != '').sum()), fichas_listado=listado_n)
    log('  publicado:', resumen)
    return resumen, cab


# ------------------------------------------------------------------------------------------------------------ main
def decidir(prev_dir, forzar=False):
    """Consulta solo la ficha centinela. Devuelve (modo, fecha_centinela, estado_publicado).
    Modos: 'barrido' (empieza a bajar todas las fichas activas), 'continuar' (sigue un barrido a medias),
    'pendientes' (reintenta fondos atrasados), None (nada que hacer)."""
    estado = {}
    if prev_dir and os.path.exists(os.path.join(prev_dir, 'estado.json')):
        estado = json.load(open(os.path.join(prev_dir, 'estado.json')))
    _, cen = bajar_ficha(CENTINELA)
    fcen = str(cen[1][0]['cartera_al']) if cen[0] == 'ok' else None
    log(f'centinela {CENTINELA}: {cen[0]} cartera al {fcen} | publicada: {estado.get("centinela")} | '
        f'cola: {len(estado.get("cola", []))} | pendientes: {len(estado.get("pendientes", []))} '
        f'(reintentos {estado.get("reintentos", 0)})')
    if forzar or not estado.get('centinela') or (fcen and fcen > estado['centinela']):
        modo = 'barrido'
    elif estado.get('cola'):
        modo = 'continuar'
    elif estado.get('pendientes') and estado.get('reintentos', 0) < MAX_REINTENTOS:
        modo = 'pendientes'
    else:
        modo = None
    return modo, fcen, estado


def prioridad(ids, nombres, prev_dir, base_dir):
    """Orden del barrido: primero los fondos más grandes (lo importante se actualiza antes). Tamaño en ARS aprox.:
    AUM del fondo en la planilla diaria (último dato, clases USD al A3500) o, si no se vincula, patrimonio publicado."""
    tam = {}
    try:
        ind = pd.read_csv(os.path.join(prev_dir, 'indice.csv'), dtype={'ficha_id': str})
        usd = ind.moneda.map(norm).str.contains('dolar', na=False)
        tam.update(dict(zip(ind.ficha_id, ind.pn.fillna(0) * np.where(usd, 1500, 1))))
    except Exception:
        pass
    try:
        m = pd.read_parquet(os.path.join(base_dir, 'maestro.parquet'))
        f = sorted(glob.glob(os.path.join(base_dir, 'clases_diario_*.parquet')))[-1]
        cd = pd.read_parquet(f, columns=['fecha', 'clase_id', 'aum'])
        cd = cd[cd.aum.notna() & (cd.fecha >= cd.fecha.max() - pd.Timedelta(days=10))].sort_values('fecha').groupby('clase_id').tail(1)
        cm = m.drop_duplicates('clase_id').set_index('clase_id')
        fx = pd.read_csv(os.path.join(base_dir, 'fx.csv')).a3500.dropna().iloc[-1]
        cd['ars'] = np.where(cd.clase_id.map(cm.moneda) == 'USD', cd.aum * fx, cd.aum)
        por_fondo = cd.groupby(cd.clase_id.map(cm.fondo_id)).ars.sum()
        m['k'] = m.fondo.map(nfondo)
        k2f = m.drop_duplicates('k').set_index('k').fondo_id
        for fid in ids:
            v = por_fondo.get(k2f.get(nfondo(nombres.get(fid, ''))), 0)
            if v and v > (tam.get(fid) or 0):
                tam[fid] = float(v)
    except Exception as e:
        log('  prioridad sin planilla:', type(e).__name__, e)
    return sorted(ids, key=lambda f: -(tam.get(f) or 0))


def main(prev_dir, out_dir, base_dir=None, forzar=False, republicar=False):
    hoy = hoy_art()
    t0 = time.time()
    if republicar:                       # recalcula derivados y CSV desde el histórico, sin bajar nada de la CNV
        estado = json.load(open(os.path.join(prev_dir, 'estado.json'))) if os.path.exists(os.path.join(prev_dir, 'estado.json')) else {}
        modo, fcen = 'republicar', estado.get('centinela')
    else:
        modo, fcen, estado = decidir(prev_dir, forzar)
    # partimos de lo publicado (el histórico se conserva entero)
    if os.path.abspath(prev_dir or '') != os.path.abspath(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
        if prev_dir and os.path.isdir(os.path.join(prev_dir, 'historico')):
            shutil.copytree(os.path.join(prev_dir, 'historico'), os.path.join(out_dir, 'historico'))
    os.makedirs(out_dir, exist_ok=True)
    hdir = os.path.join(out_dir, 'historico')
    if modo is None:
        open(os.path.join(out_dir, 'SIN_CAMBIOS'), 'w').write(f'centinela {fcen}; sin pendientes')
        log('sin cambios: no se descarga ni se publica')
        return 0

    relevado = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(hours=3)
    inactivos = set(estado.get('inactivos', []))
    nombres = estado.get('nombres', {})
    rev_inact = estado.get('inactivos_revisados')
    n_listado = estado.get('fichas_listado', 0)
    if modo == 'barrido':
        listado = listar_fondos()
        n_listado = len(listado)
        revisar = not rev_inact or (hoy - dt.date.fromisoformat(rev_inact)).days >= REVISAR_INACTIVOS_DIAS
        if revisar:
            rev_inact = str(hoy)
        nombres = dict(listado)
        cola = [f for f in prioridad([f for f, _ in listado], nombres, prev_dir, base_dir) if revisar or f not in inactivos]
        log(f'barrido nuevo: {len(cola)} fichas ({len(inactivos)} inactivas '
            f'{"incluidas (revisión mensual)" if revisar else "salteadas"})')
    elif modo == 'republicar':
        cola = list(estado.get('cola', []))
    elif modo == 'continuar':
        nombres = estado.get('nombres') or dict(listar_fondos())
        cola = prioridad(list(estado['cola']), nombres, prev_dir, base_dir)
    else:
        cola = list(estado['pendientes'])
    log(f'modo {modo}: {len(cola)} fichas en cola; presupuesto {PRESUPUESTO_MIN} min')
    res, cola = ({}, cola) if modo == 'republicar' else bajar(cola, PRESUPUESTO_MIN * 60 - (time.time() - t0))

    # control de calidad: si casi todo falla, la CNV está caída o cambió la página -> no se publica
    n = len(res)
    malas = sum(1 for r in res.values() if r[0] == 'error')
    if n >= 40 and malas > 0.5 * n:
        raise RuntimeError(f'{malas}/{n} fichas con error: no se publica (posible problema en la CNV)')
    nuevo = a_filas(res, relevado)
    if len(nuevo):
        guardar_historico(nuevo, hdir)
    hist = leer_historico(hdir)
    if hist.empty:
        raise RuntimeError('no hay carteras en el histórico')

    ultf = hist.groupby('ficha_id').fecha_cartera.max()
    fmax = ultf.max()
    for f, (st, p) in res.items():       # fichas sin cartera o con cartera muy vieja: se saltean en los próximos barridos
        if st == 'sin_cartera' or (st == 'ok' and p[0]['cartera_al'] < (fmax - pd.Timedelta(days=INACTIVO_DIAS)).date()):
            inactivos.add(f)
        elif st == 'ok':
            inactivos.discard(f)
    err = [f for f, r in res.items() if r[0] == 'error']
    if modo == 'republicar':
        pend, reint = estado.get('pendientes', []), estado.get('reintentos', 0)
    elif cola:                            # barrido a medias: los pendientes se calculan al terminar
        pend, reint = sorted(set(estado.get('pendientes', [])) | set(err)), estado.get('reintentos', 0)
    else:
        atrasadas = ultf[(ultf < fmax) & (ultf >= fmax - pd.Timedelta(days=PENDIENTE_DIAS))].index.tolist()
        pend = sorted((set(atrasadas) | set(err)) - inactivos)
        reint = estado.get('reintentos', 0) + 1 if modo == 'pendientes' else 0
    cen = fcen if modo == 'barrido' else estado.get('centinela')
    estado_n = dict(centinela=cen or estado.get('centinela'), ultima_corrida=str(relevado)[:16], modo=modo, cola=cola,
                    pendientes=pend, reintentos=reint, inactivos=sorted(inactivos), inactivos_revisados=rev_inact,
                    errores=len(err), fichas_bajadas=n, fichas_ok=sum(1 for r in res.values() if r[0] == 'ok'),
                    fichas_listado=n_listado, nombres=nombres)
    vin = vincular(hist[hist.fecha_cartera == hist.groupby('ficha_id').fecha_cartera.transform('max')], base_dir) if base_dir else \
        pd.DataFrame({'ficha_id': ultf.index, 'fondo_id': None, 'subtipo': None, 'gerente_corto': None, 'aum_planilla': np.nan})
    resumen, _ = construir(hist, vin, hoy, out_dir, n_listado)
    estado_n.update(resumen)
    json.dump(estado_n, open(os.path.join(out_dir, 'estado.json'), 'w'), ensure_ascii=False, indent=1)
    pd.DataFrame([dict(clave=k, valor=v if not isinstance(v, list) else len(v)) for k, v in estado_n.items()
                  if not isinstance(v, dict)]) \
        .to_csv(os.path.join(out_dir, 'meta.csv'), index=False)
    log('estado:', {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in estado_n.items()})
    log(f'total {time.time() - t0:.0f}s')
    return len(cola)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    forzar = '--forzar' in sys.argv
    if '--control' in sys.argv:          # solo decide si hace falta correr (para no bajar nada más si no hay novedades)
        modo, _, _ = decidir(a[0], forzar)
        log('correr:', modo or 'no')
        if os.environ.get('GITHUB_OUTPUT'):
            open(os.environ['GITHUB_OUTPUT'], 'a').write(f"run={'si' if modo else 'no'}\n")
    else:
        quedan = main(a[0], a[1], a[2] if len(a) > 2 else None, forzar=forzar, republicar='--republicar' in sys.argv)
        if os.environ.get('GITHUB_OUTPUT'):   # si quedó cola, el workflow se vuelve a disparar y sigue
            open(os.environ['GITHUB_OUTPUT'], 'a').write(f"quedan={quedan}\n")
