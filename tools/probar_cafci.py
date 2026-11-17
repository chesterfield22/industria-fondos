"""Prueba de acceso a la API de CAFCI desde GitHub Actions: ¿se puede obtener la cartera por fondo?"""
import json, re, urllib.request, urllib.error

H = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'Origin': 'https://www.cafci.org.ar',
     'Referer': 'https://www.cafci.org.ar/'}
HOSTS = ['https://api.cafci.org.ar', 'https://api.pub.cafci.org.ar']


def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60) as r:
            b = r.read(); print(f'  {r.status} {len(b):,} B  {url}'); return json.loads(b)
    except urllib.error.HTTPError as e:
        print(f'  HTTP {e.code}  {url}  {e.read()[:200]!r}')
    except Exception as e:
        print(f'  ERROR {type(e).__name__}: {e}  {url}')


def keys(o, p='', d=0):
    if d > 4: return
    if isinstance(o, dict):
        for k, v in o.items():
            print(f'    {p}{k}: {type(v).__name__}' + (f' [{len(v)}]' if isinstance(v, list) else ''))
            keys(v[0] if isinstance(v, list) and v else v, p + '  ', d + 1)


for host in HOSTS:
    print('== ', host)
    lst = get(f'{host}/fondo?estado=1&include=gerente,clase_fondo&limit=0')
    if not lst: continue
    data = lst.get('data', lst)
    print('  fondos:', len(data))
    iol = [f for f in data if re.search(r'IOL Cash|IOL D[oó]lar', f.get('nombre', ''), re.I)]
    for f in iol[:2]:
        cl = (f.get('clase_fondos') or f.get('clases') or [{}])[0]
        print('  ', f.get('id'), f.get('nombre'), '| clase', cl.get('id'), cl.get('nombre'))
        ficha = get(f"{host}/fondo/{f['id']}/clase/{cl.get('id')}/ficha")
        if ficha:
            keys(ficha)
            s = json.dumps(ficha, ensure_ascii=False)
            for m in re.finditer(r'"(cartera|carteras|composicion|activos)"', s, re.I):
                print('  >>', s[m.start(): m.start() + 600]); break
