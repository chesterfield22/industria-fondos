"""Viabilidad: planilla diaria directo de CNV (CuotaPartes -> presentación AIF -> Excel)."""
import re, json, io, time, collections, urllib.request, urllib.parse, html as H_
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36'}
def req(url, data=None, hdr=None):
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(url, data=data, headers={**UA, **(hdr or {})}), timeout=120)
    b = r.read(); print(f'  {r.status} {len(b):>10,}B {time.time()-t:5.1f}s {url[:110]}'); return b
h = req('https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/CuotaPartes').decode('utf-8', 'ignore')
rows = re.findall(r'<tr[^>]*>(.*?)</tr>', h, re.S)
docs = []
for r in rows:
    tds = [re.sub(r'\s+', ' ', H_.unescape(re.sub(r'<[^>]+>', ' ', c))).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S)]
    a = re.search(r'href="([^"]+)"', r)
    if len(tds) >= 4 and a: docs.append((tds[0], tds[1], tds[2], tds[3], a.group(1)))
print('documentos listados:', len(docs))
for d in docs[:12]: print('  ', d[:4])
print('  ...', docs[-1][:4] if docs else '')
# descargar el más reciente
url = docs[0][4]; url = url if url.startswith('http') else 'https://aif2.cnv.gov.ar' + url
p = req(url).decode('utf-8', 'ignore')
m = re.search(r'"nombreArchivo"\s*:\s*"([^"]+\.xlsx)"[\s\S]*?"guid"\s*:\s*"([0-9a-f-]{36})"', p, re.I)
print('archivo:', m and m.group(1))
vk = json.loads(req(f'https://aif2.cnv.gov.ar/api/ValetKeyProvider/GetPublicValetKey/{m.group(2)}?operation=DownloadBlob'))['valetKeyData']
x = req(f'https://blob.cnv.gov.ar/BlobWebService.svc/DownloadBlob/{m.group(2)}', data=urllib.parse.urlencode({'ValetKey': vk}).encode(),
        hdr={'Content-Type': 'application/x-www-form-urlencoded'})
import subprocess, sys; subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'openpyxl'])
import openpyxl
ws = openpyxl.load_workbook(io.BytesIO(x), read_only=True).worksheets[0]
R = list(ws.iter_rows(values_only=True))
print('filas:', len(R), '| columnas:', max(len(r) for r in R))
for r in R[:10]: print('  ', [c for c in r[:22]])
hdr_i = next(i for i, r in enumerate(R) if r and r[0] == 'Fondo')
print('encabezado fila', hdr_i, R[hdr_i][:45]); print('            ', R[hdr_i + 1][:45])
data = [r for r in R[hdr_i + 2:] if r and r[20] not in (None, '')]
print('clases con Código CAFCI:', len(data))
print('fechas (top):', collections.Counter(str(r[4]) for r in data).most_common(5))
for r in data:
    if r[0] and str(r[0]).startswith('IOL'): print('  ', r[0], r[4], r[5], r[14], 'CAFCI', r[20], 'padre', r[39] if len(r) > 39 else None)
