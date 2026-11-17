import re, urllib.request, urllib.error, urllib.parse
H = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36', 'Accept': '*/*'}
def get(url, data=None):
    try:
        req = urllib.request.Request(url, headers=H, data=data)
        with urllib.request.urlopen(req, timeout=60) as r:
            b = r.read(); print(f'{r.status} {len(b):>9,}B {r.headers.get("Content-Type","")[:28]:28} {r.geturl()}')
            return b.decode('utf-8', 'ignore'), r.geturl()
    except urllib.error.HTTPError as e:
        print(f'HTTP {e.code} {url}')
    except Exception as e:
        print(f'ERR {type(e).__name__}: {str(e)[:100]} {url}')
    return '', url
def show(html, pats, n=6, w=260):
    for p in pats:
        for i, m in enumerate(re.finditer(p, html, re.I | re.S)):
            if i >= n: break
            s = max(0, m.start() - 60); print('   ['+p+']', re.sub(r'\s+', ' ', html[s:s + w]))
B = 'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/'
h, _ = get(B + 'Busqueda')
show(h, [r'<form', r'url\s*:', r'\$\.(get|post|ajax)', r'fetch\(', r'href="[^"]*(Detalle|Fondo)[^"]*"'])
h, _ = get(B + 'CuotaPartes')
show(h, [r'href="[^"]*\.(xls|xlsx|zip|csv)', r'url\s*:', r'Planilla'])
h, _ = get('https://aif2.cnv.gov.ar/Presentations/publicview/F6E483C6-2DEC-4A6E-A290-B4A522633F37')
show(h, [r'<title', r'<h\d', r'href="[^"]+"'], n=8)
for q in ('IOL', 'IOL%20Cash'):
    for path in (f'Busqueda?q={q}', f'Busqueda?nombre={q}', f'BuscarFondos?nombre={q}', f'Busqueda/Buscar?texto={q}'):
        h, _ = get(B + path)
        if h: show(h, [r'IOL[^<]{0,80}', r'href="[^"]*(Detalle|Fondo/)[^"]*"'], n=4)
