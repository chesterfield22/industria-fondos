"""Fuente principal: planilla diaria de cuotapartes que CAFCI presenta a la CNV.

Camino (el mismo que usa ArgentinaDatos):
  1. https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/CuotaPartes  -> tabla de presentaciones AIF
     (fecha del documento, fecha/hora de recepción, descripción "... al 16 nov. 2026", id).
     La CNV recibe dos versiones por día: una la misma noche (~20:20, ~85% del AUM) y una revisada al
     mediodía del día hábil siguiente (completa). Se toma siempre la última recibida para cada fecha.
  2. Presentación AIF -> nombre y guid del Excel adjunto.
  3. aif2.cnv.gov.ar/api/ValetKeyProvider/GetPublicValetKey/{guid} -> clave temporal.
  4. blob.cnv.gov.ar/BlobWebService.svc/DownloadBlob/{guid} (POST ValetKey) -> Excel.

El "Código CAFCI" de la planilla es el clase_id de la base (mismo identificador que ArgentinaDatos), el VCP viene
en las mismas unidades ("Valor (mil cuotapartes)") y el patrimonio en la moneda de la clase.

Nada de esto es una API documentada: ante cualquier falla se devuelve vacío y la corrida sigue con ArgentinaDatos.
"""
import io, re, json, time, html, urllib.request, urllib.parse
import numpy as np, pandas as pd

LIST_URL = 'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/CuotaPartes'
AIF = 'https://aif2.cnv.gov.ar'
BLOB = 'https://blob.cnv.gov.ar/BlobWebService.svc/DownloadBlob/'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept-Language': 'es-AR,es;q=0.9'}
MESES = {'ene': 1, 'feb': 2, 'mar': 3, 'abr': 4, 'may': 5, 'jun': 6, 'jul': 7, 'ago': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dic': 12}
MONEDA = {'ARS': 'ARS', 'USD': 'USD', 'USB': 'USD'}
# columnas del Excel (0-based), validadas contra el encabezado en cada descarga
COL = dict(nombre=0, moneda=1, region=2, horizonte=3, fecha=4, vcp=5, cuotapartes=12, aum=14, depositaria=17,
           codigo_cnv=18, clase_id=20, gerente=23, com_ingreso=29, hon_gerente=30, hon_depositaria=31, gastos=32,
           com_egreso=33, plazo_liq=37, inversion_min=43)
ENCABEZADO = {0: 'Fondo', 4: 'Fecha', 5: 'Valor', 14: 'Patrimonio', 18: 'Codigo CNV', 20: 'Código CAFCI'}


def _get(url, data=None, headers=None, intentos=3, timeout=90):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if i == intentos - 1:
                raise
            print(f'  CNV reintento {i + 1} ({type(e).__name__}: {str(e)[:80]}) {url[:90]}')
            time.sleep(5 * (i + 1))


def _fecha_es(s):
    m = re.search(r'(\d{1,2})\s+([a-záéíóú]+)\.?\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?', s.lower())
    if not m or m.group(2)[:3] not in MESES:
        return None
    return pd.Timestamp(int(m.group(3)), MESES[m.group(2)[:3]], int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0))


def listar():
    """Presentaciones de la planilla diaria: fecha del documento, recepción y URL. Última versión por fecha."""
    h = _get(LIST_URL).decode('utf-8', 'ignore')
    docs = []
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', h, re.S):
        tds = [re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', c))).strip()
               for c in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)]
        a = re.search(r'href="([^"]+)"', row)
        if len(tds) < 4 or not a or 'Cuotaparte' not in tds[2]:
            continue
        m = re.search(r'al\s+(.+)$', tds[2])
        fecha = _fecha_es(m.group(1)) if m else _fecha_es(tds[0])
        if fecha is None:
            continue
        url = a.group(1) if a.group(1).startswith('http') else AIF + a.group(1)
        docs.append(dict(fecha=fecha.normalize(), recibido=_fecha_es(tds[1]), url=url, doc_id=tds[3]))
    d = pd.DataFrame(docs)
    if d.empty:
        raise RuntimeError('CNV: la página de cuotapartes no listó presentaciones')
    return d.sort_values(['fecha', 'recibido']).groupby('fecha').tail(1).sort_values('fecha').reset_index(drop=True)


def bajar_excel(url):
    p = _get(url).decode('utf-8', 'ignore')
    m = re.search(r'"nombreArchivo"\s*:\s*"([^"]+\.xlsx)"[\s\S]*?"guid"\s*:\s*"([0-9a-f-]{36})"', p, re.I)
    if not m:
        raise RuntimeError(f'CNV: la presentación no tiene Excel adjunto ({url})')
    vk = json.loads(_get(f'{AIF}/api/ValetKeyProvider/GetPublicValetKey/{m.group(2)}?operation=DownloadBlob'))['valetKeyData']
    x = _get(BLOB + m.group(2), data=urllib.parse.urlencode({'ValetKey': vk}).encode(),
             headers={'Content-Type': 'application/x-www-form-urlencoded'})
    if x[:2] != b'PK':
        raise RuntimeError(f'CNV: el archivo descargado no es un Excel ({m.group(1)})')
    return m.group(1), x


def _num(v):
    if v is None or v == '':
        return np.nan
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('.', '').replace(',', '.') if re.search(r',\d+$', str(v)) else str(v).strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse_excel(x):
    """Filas por clase (fecha, clase_id, vcp, aum) y metadatos. tipo_renta = sección de la planilla sin la moneda."""
    import openpyxl
    R = list(openpyxl.load_workbook(io.BytesIO(x), read_only=True, data_only=True).worksheets[0].iter_rows(values_only=True))
    h = next(i for i, r in enumerate(R) if r and r[0] == 'Fondo')
    for i, t in ENCABEZADO.items():
        if t.lower() not in str(R[h][i]).lower():
            raise RuntimeError(f'CNV: cambió el formato de la planilla (columna {i}: {R[h][i]!r}, se esperaba {t!r})')
    filas, seccion = [], None
    for r in R[h + 2:]:
        if not r or all(c in (None, '') for c in r[:21]):
            continue
        if r[0] and all(c in (None, '') for c in r[1:21]):
            seccion = re.sub(r'\s+(Peso Argentina|Dolar Estadounidense( Billete)?)$', '', str(r[0]).strip())
            continue
        if r[COL['clase_id']] in (None, ''):
            continue
        f = {k: r[i] if i < len(r) else None for k, i in COL.items()}
        f['tipo_renta'] = seccion
        filas.append(f)
    d = pd.DataFrame(filas)
    d['clase_id'] = d.clase_id.map(lambda v: str(int(v)) if isinstance(v, (int, float)) else str(v).strip())
    d['codigo_cnv'] = d.codigo_cnv.map(lambda v: str(int(v)) if isinstance(v, (int, float)) else (str(v).strip() if v else None))
    d['fecha'] = pd.to_datetime(d.fecha.astype(str).str.strip(), format='%d/%m/%y', errors='coerce')
    for c in ('vcp', 'aum', 'cuotapartes', 'com_ingreso', 'hon_gerente', 'hon_depositaria', 'gastos', 'com_egreso',
              'plazo_liq', 'inversion_min'):
        d[c] = d[c].map(_num)
    d['moneda'] = d.moneda.map(lambda v: MONEDA.get(str(v).strip(), 'ARS'))
    return d.dropna(subset=['fecha', 'vcp'])


def descargar(dias=10, min_clases=1500):
    """Últimas `dias` fechas publicadas (última versión de cada una). Devuelve (filas, meta, info).
    filas: fecha, clase_id, vcp, aum — sólo observaciones de la propia fecha de cada planilla (y la víspera, si la clase
    no reportó ese día, queda con su dato propio). Ante cualquier error: (None, None, info con el error)."""
    info = dict(ok=False, error='', planillas=0, ultima_fecha=None, recibida=None)
    try:
        docs = listar().tail(dias)
        parts, metas = [], []
        for _, doc in docs.iterrows():
            nombre, x = bajar_excel(doc.url)
            d = parse_excel(x)
            if len(d) < min_clases:
                raise RuntimeError(f'CNV: planilla {nombre} con sólo {len(d)} clases')
            d = d[d.fecha >= doc.fecha - pd.Timedelta(days=10)]          # datos propios de días cercanos
            d['_orden'] = doc.recibido
            parts.append(d)
            print(f'  CNV {doc.fecha.date()} recibida {doc.recibido} {nombre}: {int((d.fecha == doc.fecha).sum())} clases con dato del día')
        allf = pd.concat(parts, ignore_index=True).sort_values('_orden')
        filas = allf.drop_duplicates(['clase_id', 'fecha'], keep='last')[['fecha', 'clase_id', 'vcp', 'aum']]
        meta = allf.drop_duplicates('clase_id', keep='last').drop(columns=['fecha', 'vcp', 'aum', 'cuotapartes', '_orden'])
        last = docs.iloc[-1]
        info.update(ok=True, planillas=len(docs), ultima_fecha=str(last.fecha.date()),
                    recibida=str(last.recibido) if last.recibido is not None else None)
        return filas.reset_index(drop=True), meta.reset_index(drop=True), info
    except Exception as e:
        info['error'] = f'{type(e).__name__}: {str(e)[:200]}'
        print('  CNV no disponible ->', info['error'], '| se usa ArgentinaDatos')
        return None, None, info
