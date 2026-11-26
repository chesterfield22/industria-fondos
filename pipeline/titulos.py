"""Tenencias por título en toda la industria (hoja Títulos).

A partir de la última cartera de cada fondo (derivado/ultimas.parquet):
  1. vincula cada especie de la CNV con un ticker de BYMA (universo de tickers de la API de IOL):
     a) ticker escrito en el nombre ("Bono del Tesoro Nacional T31Y7"), b) ON por emisor + clase
     ("ON Tecpetrol C12" <-> "On Tecpetrol S. A. Cl. Xii"), c) correcciones a mano en ref/especie_ticker_manual.csv;
  2. busca el precio del día de la cartera (API de IOL, serie histórica; caché para no repetir consultas). El precio es
     CONFIABLE sólo si ese día hubo operaciones y no salta más de 15% contra la mediana de las ruedas previas;
  3. estima el valor nominal (VNO) sólo con precio confiable: VNO = monto / (precio / 100), en la moneda en que valúa
     el fondo (fondos en pesos: ticker en pesos; fondos en dólares: ticker D/MEP);
  4. como respaldo del monto en circulación toma el residual de la ficha técnica de BYMA (marcado: no validado).
El monto en circulación validado viene de la planilla Montos y se cruza en el Sheet.

Salidas (carpeta titulos/): indice.csv, t/<clave>.csv (un archivo por título), especie_ticker.csv (vínculos, para
auditar), sin_ticker.csv (lo no vinculado, ordenado por monto), cache_*.{parquet,csv}.
"""
import datetime as dt
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

IOL = 'https://api.invertironline.com'
BYMA_FICHA = 'https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/bnown/fichatecnica/especies/general'
NO_TITULO = {'Plazo fijo', 'Caución', 'Pase', 'Cheque', 'Pagaré', 'FCE'}
DEUDA = {'Soberano', 'BCRA', 'Subsoberano', 'ON', 'VCP', 'Otro título de deuda'}
SALTO_MAX = 0.15
CACHE_DIAS = 7
UA = {'User-Agent': 'industria-fondos/1.0', 'Accept': 'application/json'}


def log(*a):
    print(*a, flush=True)


def norm(s):
    import unicodedata
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', s).strip()


# ------------------------------------------------------------------------------------------------------------ IOL API
class IOLApi:
    """Cliente mínimo: token con renovación, reintentos y pausa entre consultas. Credenciales por variables de entorno
    (IOL_USER / IOL_PASS, secretos de GitHub): nunca en el código."""

    def __init__(self, user, pwd, pausa=0.25):
        self.user, self.pwd, self.pausa = user, pwd, pausa
        self.tok, self.ref, self.exp = None, None, 0
        self.n = 0

    def _token(self, refresh=False):
        data = ({'grant_type': 'refresh_token', 'refresh_token': self.ref} if refresh and self.ref else
                {'grant_type': 'password', 'username': self.user, 'password': self.pwd})
        req = urllib.request.Request(f'{IOL}/token', data=urllib.parse.urlencode(data).encode(),
                                     headers={**UA, 'Content-Type': 'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        self.tok, self.ref = j['access_token'], j.get('refresh_token')
        self.exp = time.time() + int(j.get('expires_in', 900)) - 60

    def get(self, path):
        for k in range(4):
            if not self.tok or time.time() > self.exp:
                try:
                    self._token(refresh=bool(self.ref))
                except Exception:
                    self._token(refresh=False)
            try:
                req = urllib.request.Request(f'{IOL}{path}', headers={**UA, 'Authorization': f'Bearer {self.tok}'})
                with urllib.request.urlopen(req, timeout=30) as r:
                    self.n += 1
                    time.sleep(self.pausa)
                    return json.loads(r.read() or b'null')
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.tok = None
                    continue
                if e.code in (404, 400) or (e.code == 500 and k >= 1):
                    return None
                time.sleep((2, 5, 15, 30)[k])
            except Exception:
                time.sleep((2, 5, 15, 30)[k])
        return None

    def universo(self):
        """Tickers de renta fija de BYMA con su descripción: títulos públicos, letras y ON (listado completo)."""
        filas = []
        for inst in ('titulosPublicos', 'letras', 'obligacionesNegociables'):
            j = self.get(f'/api/v2/cotizaciones-orleans/{inst}/argentina/Todos')
            tit = (j or {}).get('titulos', []) if isinstance(j, dict) else []
            for t in tit:
                if t.get('simbolo'):
                    filas.append(dict(simbolo=str(t['simbolo']).upper(), descripcion=t.get('descripcion') or '',
                                      moneda=t.get('moneda', ''), instrumento=inst, panel='Todos'))
            log(f'  IOL {inst}: {len(tit)} tickers')
        if len(filas) < 200:     # respaldo: paneles del instrumento Bonos (listan sólo lo más operado)
            for p in [x.get('panel') for x in (self.get('/api/v2/argentina/Titulos/Cotizacion/Paneles/Bonos') or [])
                      if isinstance(x, dict) and x.get('panel')]:
                j = self.get(f'/api/v2/Cotizaciones/Bonos/{urllib.parse.quote(p)}/argentina')
                for t in ((j or {}).get('titulos', []) if isinstance(j, dict) else []):
                    if t.get('simbolo'):
                        filas.append(dict(simbolo=str(t['simbolo']).upper(), descripcion=t.get('descripcion') or '',
                                          moneda=t.get('moneda', ''), instrumento='Bonos', panel=p))
        return pd.DataFrame(filas).drop_duplicates('simbolo') if filas else pd.DataFrame(
            columns=['simbolo', 'descripcion', 'moneda', 'instrumento', 'panel'])

    def serie(self, simbolo, d0, d1):
        j = self.get(f'/api/v2/bCBA/Titulos/{urllib.parse.quote(simbolo)}/Cotizacion/seriehistorica/{d0}/{d1}/sinAjustar')
        if not isinstance(j, list):
            return pd.DataFrame()
        rows = []
        for x in j:
            f = str(x.get('fechaHora') or x.get('fecha') or '')[:10]
            p = x.get('ultimoPrecio', x.get('cierre'))
            if not f or p is None:
                continue
            rows.append(dict(simbolo=simbolo, fecha=f, precio=float(p),
                             operaciones=x.get('cantidadOperaciones'), volumen=x.get('volumenNominal'),
                             monto=x.get('montoOperado'), fuente='IOL'))
        d = pd.DataFrame(rows)
        if len(d):
            d['fecha'] = pd.to_datetime(d.fecha)
            d = d.sort_values('fecha').groupby('fecha', as_index=False).last()
        return d


# ------------------------------------------------------------------------------------------------------------ vínculo
ROM = {'i': 1, 'v': 5, 'x': 10, 'l': 50, 'c': 100}


def roman(s):
    s = s.lower()
    if not s or any(ch not in ROM for ch in s):
        return None
    tot = 0
    for a, b in zip(s, s[1:] + ' '):
        v = ROM[a]
        tot += -v if b in ROM and ROM[b] > v else v
    return tot


def clase_num(tok):
    tok = tok.strip('.').strip()
    if tok.isdigit():
        return str(int(tok))
    r = roman(tok)
    if r and len(tok) > 1:
        return str(r)
    return tok.upper() if re.fullmatch(r'[A-Za-z]', tok) else None


STOP = {'on', 'sa', 's', 'a', 'sau', 'srl', 'de', 'del', 'la', 'el', 'los', 'y', 'banco', 'bco', 'cia', 'compania',
        'argentina', 'arg', 'sociedad', 'anonima', 'grupo', 'the', 'inc', 'corp', 'energia', 'energy', 'clase', 'cl',
        'serie', 'vto', 'u', 'sl', 'sucursal'}


def toks(s):
    return {t for t in re.findall(r'[a-z0-9]+', norm(s)) if t not in STOP and not t.isdigit() and len(t) > 1}


RX_CL_CNV = re.compile(r'\s(?:C|Cl|CL|Clase|S|Serie|Ser)\.?\s?([0-9]+|[IVXLC]{1,6}|[A-Z])\b')
RX_CL_IOL = re.compile(r'\b(?:Cl|Clase|Cla|Serie|S|Ser)\.?\s?([0-9]+|[IVXLCivxlc]{1,6}|[A-Za-z])\b')


def parse_on_cnv(esp):
    s = re.sub(r'^ON\s+', '', esp or '')
    m = RX_CL_CNV.search(' ' + s)
    if not m:
        return None, None
    return s[: max(m.start() - 1, 0)], clase_num(m.group(1))


def resto_cnv(esp):
    s = ' ' + re.sub(r'^ON\s+', '', esp or '')
    m = RX_CL_CNV.search(s)
    return s[m.end():] if m else ''


RX_SOC = re.compile(r'\b(S\.?\s?A\.?(\s?U\.?)?|S\.?\s?R\.?\s?L\.?|S\.?\s?L\.?)(?=\s|$)', re.I)


def parse_on_iol(desc):
    s = re.sub(r'^O\.?N\.?\s+', '', desc or '', flags=re.I)
    s = RX_SOC.sub(' ', s)
    m = re.search(r'\b(?:Cl|Clase|Cla)\.?\s?([0-9]+|[IVXLCivxlc]{1,6}|[A-Za-z])\b', s, re.I) or RX_CL_IOL.search(s)
    if not m:
        return None, None
    return s[: m.start()], clase_num(m.group(1))


def resto_iol(desc):
    s = RX_SOC.sub(' ', re.sub(r'^O\.?N\.?\s+', '', desc or '', flags=re.I))
    m = re.search(r'\b(?:Cl|Clase|Cla)\.?\s?([0-9]+|[IVXLCivxlc]{1,6}|[A-Za-z])\b', s, re.I) or RX_CL_IOL.search(s)
    return s[m.end():] if m else ''


RX_SERIE = re.compile(r'\bS(?:erie|er)?\.?\s?([0-9]{1,2}|[IVX]{1,4})\b', re.I)


def serie_num(resto):
    """Número de serie dentro de una clase ("C67 S1", "Cl.67 S.1"): se busca sólo después de la clase."""
    m = RX_SERIE.match((resto or '').strip())
    return clase_num(m.group(1)) if m else None


def base_ars(simbolos, sym):
    """De una familia O/D/C elige el ticker en pesos."""
    if sym.endswith(('D', 'C')) and len(sym) >= 4:
        for c in (sym[:-1] + 'O', sym[:-1] + 'P', sym[:-1]):
            if c in simbolos:
                return c
    return sym


def ticker_usd(simbolos, sym, por_desc=None, desc_de=None):
    """Ticker en dólares MEP (D) del mismo título. Si no sigue la regla de sufijo (BOPREAL: BPOD7 → BPD7D),
    se busca el ticker terminado en D con la misma descripción en el universo de IOL."""
    for c in ([sym[:-1] + 'D'] if sym.endswith('O') else []) + [sym + 'D']:
        if c in simbolos:
            return c
    if por_desc and desc_de and desc_de.get(sym):
        cand = sorted(t for t in por_desc.get(desc_de[sym], []) if t != sym and t.endswith('D'))
        if len(cand) == 1:
            return cand[0]
    return None


RX_VTO = re.compile(r'(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b')


def vto_desc(desc):
    """Vencimiento escrito en la descripción de IOL (\"Vto. 28/7/2025\", \"V17/01/28\")."""
    m = RX_VTO.search(desc or '')
    if not m:
        return None
    d, mth, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    y = y + 2000 if y < 100 else y
    try:
        return dt.date(y, mth, d)
    except ValueError:
        return None


def vincular_especies(esp, uni, manual, fecha_ref=None):
    """esp: DataFrame con especie, tipo, emisor. Devuelve especie -> (ticker, metodo)."""
    simb = set(uni.simbolo)
    out = {}
    # c) manual (prioridad)
    for e, t in manual.items():
        t, nota = t if isinstance(t, tuple) else (t, '')
        out[e] = (t.upper() if t else None, 'manual' + (f': {nota}' if nota else ''))
    # a) ticker en el nombre
    for e in esp.especie.unique():
        if e in out:
            continue
        for t in re.findall(r'\b([A-Z][A-Z0-9]{2,5})\b', e or ''):
            if t in simb and not t.isdigit():
                out[e] = (base_ars(simb, t), 'ticker en el nombre')
                break
    # b) ON por emisor + clase
    es_on = uni.descripcion.str.match(r'^\s*O\.?\s?N\.?\s', case=False, na=False)
    if 'instrumento' in uni:
        es_on |= uni.instrumento.astype(str).str.contains('obligacion', case=False, na=False)
    on = uni[es_on].copy()
    on[['emi', 'cl']] = on.descripcion.apply(lambda d: pd.Series(parse_on_iol(d)))
    on = on[on.cl.notna()]
    on = on[on.simbolo.str.fullmatch(r'[A-Z0-9]{3,6}', na=False)]
    on['ser'] = on.descripcion.map(lambda d: serie_num(resto_iol(d)))
    if fecha_ref is not None:    # una clase vencida antes de la cartera no puede estar en ella (YPF C39 2025 vs 2030)
        v_ = on.descripcion.map(vto_desc)
        on = on[[x is None or x >= fecha_ref for x in v_]]
    on['dnorm'] = on.descripcion.map(norm)
    on['tk'] = on.emi.map(toks)
    on['base'] = on.simbolo.map(lambda s: base_ars(simb, s))
    for e in esp[esp.tipo.isin(['ON', 'VCP'])].especie.unique():
        if e in out:
            continue
        emi, cl = parse_on_cnv(e)
        if not cl:
            continue
        ser = serie_num(resto_cnv(e))
        te = toks(emi)
        if not te:
            continue
        c = on[on.cl == cl]
        sc, desc_de = {}, {}
        for _, r in c.iterrows():
            inter = te & r.tk
            if not inter or not any(len(t) >= 3 for t in inter):
                continue
            v = len(inter) / min(len(te), len(r.tk) or 1)
            if v >= 0.5:
                if ser and r.ser and ser != r.ser:   # misma clase, distinta serie
                    continue
                # desempates: misma serie informada; nombre de emisor más parecido (Jaccard: "YPF" ≠ "YPF Luz")
                k = (round(v, 6), int(bool(ser and r.ser == ser)), round(len(inter) / len(te | r.tk), 6))
                sc[r.base] = max(sc.get(r.base, (0, 0, 0)), k)
                desc_de[r.base] = r.dnorm
        if not sc:
            continue
        orden = sorted(sc.items(), key=lambda x: x[1], reverse=True)
        top = [k_ for k_, v_ in orden if v_ == orden[0][1]]
        if len(top) > 1 and len({desc_de[k_] for k_ in top}) == 1:   # mismo título en otra moneda de liquidación
            top = sorted(top, key=lambda t: (not t.endswith(('O', 'P')), t.endswith('C'), t))[:1]
            orden = [(top[0], orden[0][1])]
        if len(orden) == 1 or orden[0][1] > orden[1][1]:
            out[e] = (orden[0][0], 'emisor + clase')
        else:
            out[e] = (None, 'ambiguo: ' + ', '.join(k for k, v in orden if v == orden[0][1])[:60])
    return out


# ------------------------------------------------------------------------------------------------------------ precios
BYMA_HIST = 'https://open.bymadata.com.ar/vanoms-be-core/rest/api/bymadata/free/chart/historical-series/history'


def serie_byma(simbolo, d0, d1):
    """Respaldo de precios: serie diaria de BYMA (plazo 24HS = T+1). Cierre y volumen nominal por rueda.
    Se usa cuando la API de IOL no da la serie (p. ej. error 500 fijo en CER/TAMAR/capitalizables)."""
    t0 = int(dt.datetime.combine(d0, dt.time(3)).replace(tzinfo=dt.timezone.utc).timestamp())
    t1 = int(dt.datetime.combine(d1, dt.time(3)).replace(tzinfo=dt.timezone.utc).timestamp()) + 86400
    q = urllib.parse.urlencode({'symbol': f'{simbolo} 24HS', 'resolution': 'D', 'from': t0, 'to': t1})
    for k in range(3):
        try:
            req = urllib.request.Request(f'{BYMA_HIST}?{q}', headers={**UA, 'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            break
        except Exception:
            time.sleep((2, 5, 10)[k])
    else:
        return pd.DataFrame()
    time.sleep(0.3)
    if not isinstance(j, dict) or j.get('s') != 'ok' or not j.get('t'):
        return pd.DataFrame()
    ts, cs, vs = j['t'], j.get('c') or [], j.get('v') or []
    rows = [dict(simbolo=simbolo, fecha=dt.datetime.fromtimestamp(t - 3 * 3600, dt.timezone.utc).date().isoformat(),
                 precio=float(c), operaciones=None, volumen=(float(v) if v is not None else None), monto=None,
                 fuente='BYMA') for t, c, v in zip(ts, cs, vs + [None] * (len(ts) - len(vs))) if c is not None]
    d = pd.DataFrame(rows)
    if len(d):
        d['fecha'] = pd.to_datetime(d.fecha)
        d = d.sort_values('fecha').groupby('fecha', as_index=False).last()
    return d


def precio_confiable(serie, fecha):
    """Precio del día de la cartera sólo si hubo operaciones ese día y no salta > 15% vs. la mediana previa."""
    if serie is None or serie.empty:
        return None, 'sin serie de precios'
    d = serie[serie.fecha == fecha]
    if d.empty:
        return None, 'sin precio el día de la cartera'
    r = d.iloc[-1]
    oper = [x for x in (r.get('operaciones'), r.get('volumen'), r.get('monto')) if x is not None and pd.notna(x)]
    if oper and max(float(x) for x in oper) <= 0:
        return None, 'sin operaciones el día de la cartera'
    prev = serie[(serie.fecha < fecha)].tail(5).precio
    if len(prev) >= 2 and prev.median() > 0 and abs(r.precio / prev.median() - 1) > SALTO_MAX:
        return None, f'salto de {r.precio / prev.median() - 1:+.0%} vs. ruedas previas'
    fu = r.get('fuente') if 'fuente' in r.index else None
    return float(r.precio), 'precio del día con operaciones' + (f' ({fu})' if isinstance(fu, str) and fu else '')


def byma_ficha(ticker):
    """Residual y nominal de la ficha técnica de BYMA (respaldo, no validado)."""
    body = json.dumps({'symbol': ticker, 'Content-Type': 'application/json'}).encode()
    for k in range(2):
        try:
            req = urllib.request.Request(BYMA_FICHA, data=body, headers={**UA, 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=30) as r:
                j = json.loads(r.read())
            d = (j or {}).get('data') or []
            if d:
                x = d[0]
                return dict(ticker=ticker, byma_residual=x.get('montoResidual'), byma_nominal=x.get('montoNominal'),
                            byma_emisor=x.get('emisor'), byma_denominacion=x.get('denominacion'),
                            byma_moneda=x.get('moneda'), byma_vto=str(x.get('fechaVencimiento') or '')[:10])
            return dict(ticker=ticker)
        except Exception:
            time.sleep(3)
    return None


# ------------------------------------------------------------------------------------------------------------ principal
def es_tk(t):
    return isinstance(t, str) and bool(t)


def clave(ticker, especie):
    if es_tk(ticker):
        return ticker
    return 'e' + hashlib.md5(norm(especie).encode()).hexdigest()[:10]


def fmt(v):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return ''
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    if isinstance(v, (float, np.floating)):
        v = float(v)
        return f'{v:.0f}' if abs(v) >= 1e6 else f'{round(v, 6):.6f}'.rstrip('0').rstrip('.')
    if isinstance(v, (pd.Timestamp, dt.date)):
        return str(v)[:10]
    return str(v)


def construir_titulos(out_dir, prev_dir, base_dir, repo_dir='.', fresca_dias=14):
    t0 = time.time()
    tdir = os.path.join(out_dir, 'titulos')
    pdir = os.path.join(prev_dir or '', 'titulos')
    os.makedirs(os.path.join(tdir, 't'), exist_ok=True)
    for f in os.listdir(os.path.join(tdir, 't')):
        os.remove(os.path.join(tdir, 't', f))
    u = pd.read_parquet(os.path.join(out_dir, 'derivado', 'ultimas.parquet'))
    ind = pd.read_csv(os.path.join(out_dir, 'indice.csv'), dtype={'ficha_id': str})
    lab = ind.set_index('ficha_id').etiqueta
    fmax = u.fecha_cartera.max()
    e = u[(u.nivel == 'especie') & ~u.tipo.isin(NO_TITULO) & (u.fecha_cartera >= fmax - pd.Timedelta(days=fresca_dias))].copy()
    e['monto'] = e.pct / 100 * e.pn
    usd_f = e.moneda_fondo.map(norm).str.contains('dolar', na=False)
    fx = pd.read_csv(os.path.join(base_dir, 'fx.csv'), parse_dates=['fecha']).set_index('fecha').a3500.sort_index() \
        if base_dir and os.path.exists(os.path.join(base_dir, 'fx.csv')) else pd.Series(dtype=float)
    tc = e.fecha_cartera.map(fx.reindex(fx.index.union(e.fecha_cartera.unique())).ffill()) if len(fx) else np.nan
    e['monto_ars'] = np.where(usd_f, e.monto * tc, e.monto)
    e['fondo_usd'] = usd_f

    # --- universo de tickers (caché semanal) y vínculo
    user, pwd = os.environ.get('IOL_USER'), os.environ.get('IOL_PASS')
    api = IOLApi(user, pwd) if user and pwd else None
    uni_p = os.path.join(pdir, 'cache_universo.csv')
    uni = pd.read_csv(uni_p) if os.path.exists(uni_p) else pd.DataFrame()
    viejo = uni.empty or (dt.date.today() - dt.date.fromisoformat(str(uni.cargado.iloc[0]))).days >= CACHE_DIAS
    if api and viejo:
        try:
            nu = api.universo()
            if len(nu) > 200:
                uni = nu.assign(cargado=str(dt.date.today()))
            else:
                log(f'  universo IOL con {len(nu)} tickers: se mantiene la caché')
        except Exception as ex:
            log('  universo IOL falló:', type(ex).__name__, ex)
    if uni.empty:
        uni = pd.DataFrame(columns=['simbolo', 'descripcion', 'moneda', 'instrumento', 'panel', 'cargado'])
        log('  sin universo de tickers (faltan credenciales de IOL o la API no respondió): títulos sin ticker ni VN')
    uni['simbolo'] = uni.simbolo.astype(str).str.upper()
    uni.to_csv(os.path.join(tdir, 'cache_universo.csv'), index=False)
    manual = {}
    mp = os.path.join(repo_dir, 'ref', 'especie_ticker_manual.csv')
    if os.path.exists(mp):
        m = pd.read_csv(mp, dtype=str).fillna('')
        manual = {r.especie: (r.ticker, r.get('nota', '') or '') for _, r in m.iterrows() if r.especie}
    vin = (vincular_especies(e.drop_duplicates('especie'), uni, manual, fecha_ref=e.fecha_cartera.min().date())
           if len(uni) or manual else {})
    e['ticker'] = e.especie.map(lambda x: (vin.get(x) or (None, None))[0]).astype(object)
    e.loc[~e.ticker.map(es_tk), 'ticker'] = None
    e['vinculo'] = e.especie.map(lambda x: (vin.get(x) or (None, 'sin vínculo'))[1])

    # --- precios (caché por ticker y fecha; nunca se vuelve a pedir un día ya bajado)
    simb = set(uni.simbolo)
    desc_de = {k: norm(v) for k, v in zip(uni.simbolo, uni.descripcion.fillna('')) if norm(v)}
    por_desc = {}
    for k, v in desc_de.items():
        por_desc.setdefault(v, []).append(k)
    e['tk_precio'] = [None if not es_tk(t) or t not in simb else (ticker_usd(simb, t, por_desc, desc_de) if u_ else t)
                      for t, u_ in zip(e.ticker, e.fondo_usd)]
    e.loc[~e.tipo.isin(DEUDA), 'tk_precio'] = None          # el VN sólo tiene sentido para títulos de deuda
    cp = os.path.join(pdir, 'cache_precios.parquet')
    cache = pd.read_parquet(cp) if os.path.exists(cp) else pd.DataFrame(columns=['simbolo', 'fecha', 'precio',
                                                                                   'operaciones', 'volumen', 'monto', 'fuente'])
    pedir = e[e.tk_precio.notna()].groupby('tk_precio').fecha_cartera.agg(['min', 'max'])
    nuevos, n_byma = [], 0
    if api:
        for sym, r in pedir.iterrows():
            ya = cache[(cache.simbolo == sym)]
            if len(ya) and r['max'] in set(ya.fecha) and r['min'] in set(ya.fecha):
                continue
            d0, d1 = (r['min'] - pd.Timedelta(days=12)).date(), (r['max'] + pd.Timedelta(days=1)).date()
            s = api.serie(sym, d0, d1)
            if not len(s) or r['max'] not in set(s.fecha):
                sb = serie_byma(sym, d0, d1)
                if len(sb):
                    s = sb if not len(s) else pd.concat([s, sb[~sb.fecha.isin(set(s.fecha))]], ignore_index=True)
                    n_byma += 1
            if len(s):
                nuevos.append(s)
            else:   # se registra la ausencia para no volver a pedirla
                nuevos.append(pd.DataFrame([dict(simbolo=sym, fecha=r['max'], precio=np.nan)]))
        log(f'  precios: {len(pedir)} tickers, {len(nuevos)} con serie ({api.n} llamadas IOL, {n_byma} completados con BYMA)')
    if nuevos:
        cache = pd.concat([cache] + nuevos, ignore_index=True)
        cache['fecha'] = pd.to_datetime(cache.fecha)
        cache = cache.drop_duplicates(['simbolo', 'fecha'], keep='last')
    cache.to_parquet(os.path.join(tdir, 'cache_precios.parquet'), index=False)
    series = {s: g.dropna(subset=['precio']).sort_values('fecha') for s, g in cache.groupby('simbolo')}
    pc = {}
    for sym, f in e[e.tk_precio.notna()][['tk_precio', 'fecha_cartera']].drop_duplicates().itertuples(index=False):
        pc[(sym, f)] = precio_confiable(series.get(sym), f)
    e['precio'] = [pc.get((s, f), (None, ''))[0] if s else None for s, f in zip(e.tk_precio, e.fecha_cartera)]
    e['precio_estado'] = [pc.get((s, f), (None, ''))[1] if s else ('sin ticker' if e_t in DEUDA else 'no aplica')
                          for s, f, e_t in zip(e.tk_precio, e.fecha_cartera, e.tipo)]
    e['vno'] = e.monto / (e.precio / 100)

    # --- residual BYMA (respaldo; caché semanal)
    bp = os.path.join(pdir, 'cache_byma.csv')
    byma = pd.read_csv(bp) if os.path.exists(bp) else pd.DataFrame(columns=['ticker', 'consultado'])
    tks = sorted(set(e[e.tipo.isin(DEUDA) & e.ticker.notna()].ticker))
    frescos = set(byma[pd.to_datetime(byma.consultado, errors='coerce') >= pd.Timestamp.today() - pd.Timedelta(days=CACHE_DIAS)].ticker) \
        if len(byma) else set()
    falta = [t for t in tks if t not in frescos]
    nb, fallas = [], 0
    for t in falta:
        r = byma_ficha(t)
        if r is None:
            fallas += 1
            if fallas >= 5 and not nb:
                log('  BYMA no responde: se usa la caché'); break
            continue
        nb.append({**r, 'consultado': str(dt.date.today())})
    if nb:
        byma = pd.concat([byma[~byma.ticker.isin([x['ticker'] for x in nb])], pd.DataFrame(nb)], ignore_index=True)
    log(f'  BYMA ficha técnica: {len(nb)} consultadas, {fallas} fallas')
    byma.to_csv(os.path.join(tdir, 'cache_byma.csv'), index=False)
    bym = byma.set_index('ticker') if len(byma) else pd.DataFrame()

    # --- salidas
    e['clave'] = [clave(t, s) for t, s in zip(e.ticker, e.especie)]
    e['fondo_lbl'] = e.ficha_id.map(lab).fillna(e.fondo)
    e['gerente_lbl'] = e.gerente_corto.fillna(e.gerente)
    idx = []
    for k, g in e.groupby('clave'):
        g = g.sort_values('monto_ars', ascending=False)
        tk = g.ticker.iloc[0] if es_tk(g.ticker.iloc[0]) else None
        nombre = g.especie.value_counts().index[0]
        b = bym.loc[tk] if tk and len(bym) and tk in bym.index else None
        vno_ok = g.vno.notna()
        idx.append(dict(
            clave=k, etiqueta=(f'{tk} · {nombre}' if tk and tk not in nombre else nombre), ticker=tk or '',
            especie=nombre, tipo=g.tipo.iloc[0], emisor=g.emisor.iloc[0] or '', moneda=g.moneda.iloc[0] or '',
            n_fondos=g.ficha_id.nunique(), monto_ars=g.monto_ars.sum(),
            vno_est=g.vno.sum() if vno_ok.any() else np.nan,
            cobertura_vno=(g.monto_ars[vno_ok].sum() / g.monto_ars.sum()) if g.monto_ars.sum() else np.nan,
            fecha_cartera=g.fecha_cartera.max().date(), vinculo=g.vinculo.iloc[0],
            byma_residual=(b.get('byma_residual') if b is not None else np.nan),
            byma_nominal=(b.get('byma_nominal') if b is not None else np.nan),
            byma_consultado=(b.get('consultado') if b is not None else '')))
        g[['fondo_lbl', 'gerente_lbl', 'subtipo', 'moneda_fondo', 'pct', 'monto', 'monto_ars', 'vno', 'precio',
           'tk_precio', 'precio_estado', 'fecha_cartera', 'especie', 'ficha_id']].to_csv(
            os.path.join(tdir, 't', f'{k}.csv'), index=False, float_format='%.6f', date_format='%Y-%m-%d')
    ix = pd.DataFrame(idx).sort_values('monto_ars', ascending=False)
    with open(os.path.join(tdir, 'indice.csv'), 'w', encoding='utf-8') as fh:
        fh.write(','.join(ix.columns) + '\n')
        for r in ix.itertuples(index=False):
            fh.write(','.join('"' + fmt(v).replace('"', "'") + '"' if isinstance(v, str) else fmt(v) for v in r) + '\n')
    ev = e.drop_duplicates('especie')[['especie', 'tipo', 'ticker', 'vinculo']]
    ev.to_csv(os.path.join(tdir, 'especie_ticker.csv'), index=False)
    sv = e[e.tipo.isin(DEUDA) & e.ticker.isna()].groupby(['especie', 'tipo', 'vinculo']).monto_ars.sum() \
        .sort_values(ascending=False).reset_index()
    sv.to_csv(os.path.join(tdir, 'sin_ticker.csv'), index=False, float_format='%.0f')
    deuda = e[e.tipo.isin(DEUDA)]
    res = dict(titulos=len(ix), con_ticker=int((ix.ticker != '').sum()),
               deuda_vinculada=round(float(deuda.monto_ars[deuda.ticker.notna()].sum() / deuda.monto_ars.sum()), 3) if len(deuda) else None,
               deuda_con_vno=round(float(deuda.monto_ars[deuda.vno.notna()].sum() / deuda.monto_ars.sum()), 3) if len(deuda) else None,
               segundos=round(time.time() - t0))
    log('  títulos:', res)
    return res
