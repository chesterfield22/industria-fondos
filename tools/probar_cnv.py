"""Prueba: ¿la CNV publica la cartera semanal de cada FCI de forma accesible? Explora el sitio de la CNV."""
import re, urllib.request, urllib.error, urllib.parse
H = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36', 'Accept': '*/*'}
seen = set()

def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60) as r:
            b = r.read(); ct = r.headers.get('Content-Type', '')
            print(f'{r.status} {len(b):>9,}B {ct[:30]:30} {r.geturl()}'); return b.decode('utf-8', 'ignore'), r.geturl()
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code} {url} {e.read()[:120]!r}')
    except Exception as e:
        print(f'ERR {type(e).__name__}: {str(e)[:120]} {url}')
    return None, url

def links(html, base, pat):
    out = []
    for h in re.findall(r'(?:href|src|action)=["\']([^"\']+)', html, re.I):
        u = urllib.parse.urljoin(base, h)
        if re.search(pat, u, re.I) and u not in seen:
            seen.add(u); out.append(u)
    return out

START = ['https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion',
         'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/Fondos',
         'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/FondosComunesInversion',
         'https://www.cnv.gov.ar/SitioWeb/Entidades/Fondos',
         'https://aif2.cnv.gov.ar/presentations/publicview',
         'https://www.cnv.gov.ar/SitioWeb/']
PAT = r'fondo|fci|cartera|composici|planilla|aif'
for u in START:
    html, base = get(u)
    if not html: continue
    ls = links(html, base, PAT)
    print('   links:', len(ls))
    for l in ls[:40]: print('     ', l)
    for l in [x for x in ls if re.search(r'cartera|composici', x, re.I)][:10]:
        h2, b2 = get(l)
        if h2:
            for x in links(h2, b2, r'cartera|composici|\.xls|descarg|download')[:20]: print('        ->', x)
    for m in re.finditer(r'.{80}(cartera|Composici).{120}', html, re.I | re.S):
        print('   txt:', re.sub(r'\s+', ' ', m.group(0))[:200]); break
