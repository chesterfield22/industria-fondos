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
import subprocess, sys; subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'openpyxl'])
import openpyxl
def bajar(u):
    u = u if u.startswith('http') else 'https://aif2.cnv.gov.ar' + u
    p = req(u).decode('utf-8', 'ignore')
    m = re.search(r'"nombreArchivo"\s*:\s*"([^"]+\.xlsx)"[\s\S]*?"guid"\s*:\s*"([0-9a-f-]{36})"', p, re.I)
    vk = json.loads(req(f'https://aif2.cnv.gov.ar/api/ValetKeyProvider/GetPublicValetKey/{m.group(2)}?operation=DownloadBlob'))['valetKeyData']
    x = req(f'https://blob.cnv.gov.ar/BlobWebService.svc/DownloadBlob/{m.group(2)}', data=urllib.parse.urlencode({'ValetKey': vk}).encode(), hdr={'Content-Type': 'application/x-www-form-urlencoded'})
    R = list(openpyxl.load_workbook(io.BytesIO(x), read_only=True).worksheets[0].iter_rows(values_only=True))
    h = next(i for i, r in enumerate(R) if r and r[0] == 'Fondo')
    return {str(r[20]): (str(r[4]), r[5], r[14] or 0, r[25]) for r in R[h + 2:] if r and r[20] not in (None, '')}
for k in range(0, 8, 2):
    late, early = docs[k], docs[k + 1]
    A, B = bajar(early[4]), bajar(late[4])
    d = re.search(r'al (.+)$', late[2]).group(1)
    dd = time.strftime('%d/%m/%y', time.strptime(d.replace('.', ''), '%d %b %Y')) if False else None
    def stats(X):
        fe = collections.Counter(v[0] for v in X.values()).most_common(1)[0][0]
        n = sum(v[0] == fe and (v[2] or 0) > 0 for v in X.values())
        aum_fe = sum(v[2] for v in X.values() if v[0] == fe and v[2])   # sin TC: suma nominal (solo comparativa)
        return fe, n, aum_fe
    fa, na, aa = stats(A); fb, nb, ab = stats(B)
    cambios = sum(1 for c in A if c in B and A[c][0] == fa and B[c][0] == fb and A[c][1] and B[c][1] and abs(A[c][1] / B[c][1] - 1) > 1e-9)
    print(f'{d}: 1ra versión ({early[1]}) clases con dato {na}  | revisada ({late[1]}) {nb}  | AUM nominal 1ra/revisada {aa/ab:.4f} | VCP cambiados {cambios}')
