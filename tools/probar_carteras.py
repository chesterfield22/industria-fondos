"""Prueba descartable: ¿se pueden bajar las carteras semanales de cada FCI desde la CNV (DetallesFCI) y/o el sitio
nuevo de estadísticas de CAFCI, desde GitHub Actions? No toca el pipeline. Deja todo en probe/ (se sube como artifact)."""
import html, json, os, random, re, time, urllib.error, urllib.parse, urllib.request

OUT = 'probe'
os.makedirs(OUT, exist_ok=True)
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36',
      'Accept-Language': 'es-AR,es;q=0.9', 'Accept': 'text/html,application/json,*/*'}
BASE = 'https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion'
LOG = open(f'{OUT}/reporte.txt', 'w')


def log(*a):
    s = ' '.join(str(x) for x in a)
    print(s); LOG.write(s + '\n'); LOG.flush()


def get(url, data=None, headers=None, timeout=60):
    t = time.time()
    try:
        req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            return r.status, b, time.time() - t, r.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:500], time.time() - t, url
    except Exception as e:
        return f'{type(e).__name__}: {str(e)[:100]}', b'', time.time() - t, url


def txt(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s))).strip()


def num(s):
    s = s.strip()
    if not s:
        return None
    try:
        return float(s.replace('.', '').replace(',', '.'))
    except ValueError:
        return None


# 1. listado de fondos ------------------------------------------------------------------------------------------
log('== 1. Página de FCI de la CNV')
st, b, dt, u = get(BASE)
h = b.decode('utf-8', 'ignore')
open(f'{OUT}/listado.html', 'w').write(h)
log(st, f'{len(b):,} B {dt:.1f}s {u}')
for f in re.findall(r'<form[^>]*>', h, re.I):
    log('  form:', f[:200])
selects = {}
for m in re.finditer(r'<select([^>]*)>(.*?)</select>', h, re.S | re.I):
    attrs, body = m.group(1), m.group(2)
    name = re.search(r'(?:name|id)="([^"]+)"', attrs)
    opts = re.findall(r'<option[^>]*value="([^"]*)"[^>]*>(.*?)</option>', body, re.S | re.I)
    selects[name.group(1) if name else attrs[:40]] = [(v, txt(t)) for v, t in opts]
    log(f'  select {name.group(1) if name else attrs[:60]!r}: {len(opts)} opciones; ej: {[(v, txt(t)) for v, t in opts[:4]]}')
for s in sorted(set(re.findall(r'(?:src|href|url|action)\s*[:=]\s*["\']([^"\']+)', h, re.I))):
    if re.search(r'fondo|fci|api|json|detalle|buscar|\.js', s, re.I):
        log('  ref:', s[:160])
for m in re.finditer(r'.{0,120}(DetallesFCI|ajax|fetch\(|\$\.get|\$\.post).{0,200}', h, re.I):
    log('  js:', re.sub(r'\s+', ' ', m.group(0))[:320])
json.dump(selects, open(f'{OUT}/selects.json', 'w'), ensure_ascii=False, indent=1)


# 2. página de detalle: ¿hace falta la query string? -------------------------------------------------------------
def parse_detalle(h):
    """Cabecera + filas de la composición semanal."""
    i = h.find('Composición Semanal Cartera')
    if i < 0:
        i = h.find('Composici&oacute;n Semanal Cartera')
    if i < 0:
        return None
    sec = h[i: i + 400_000]
    j = sec.find('VOLVER')
    sec = sec[: j if j > 0 else len(sec)]
    t = txt(sec)
    cab = {}
    for k in ('Fondo', 'Cartera al \\(\\*\\)', 'Gerente', 'Depositaria', 'Clasificación', 'Moneda', 'Región', 'Horizonte'):
        m = re.search(k + r'\s*·?\s*(.+?)(?=\s(?:Fondo|Cartera al|Gerente|Depositaria|Clasificación|Moneda|Región|Horizonte|COMPOSICIÓN|Detalle)\b)', t)
        cab[k.replace('\\(\\*\\)', '').strip()] = m.group(1).strip(' ·') if m else None
    filas = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', sec, re.S | re.I):
        tds = [txt(c) for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', tr, re.S | re.I)]
        if tds and any(tds):
            filas.append(tds)
    return cab, filas


def resumen(filas):
    """Separa rubros (subtotal en col 3) de especies (col 2). Devuelve especies, rubros, suma especies, total, PN."""
    esp, rub, pn, total = [], [], None, None
    for f in filas:
        f = f + [''] * (3 - len(f))
        a, p1, p2 = f[0], num(f[1]), num(f[2])
        if a.lower().startswith('detalle'):
            continue
        if a.startswith('(') and p1 is not None:
            pn = (a, p1); continue
        if not a and p2 is not None:
            total = p2; continue
        if p1 is not None:
            esp.append((a, p1))
        else:
            rub.append((a, p2))
    return esp, rub, round(sum(p for _, p in esp), 2), total, pn


log('\n== 2. Detalle: con y sin query string')
for url in (f'{BASE}/DetallesFCI/63480?denominacion=Pionero+Renta+Ahorro&estado=Aprobado+Lanzado',
            f'{BASE}/DetallesFCI/63480'):
    st, b, dt, u = get(url)
    r = parse_detalle(b.decode('utf-8', 'ignore')) if st == 200 else None
    log(st, f'{len(b):,} B {dt:.1f}s', url, '| cartera:', (r[0].get('Cartera al'), len(r[1])) if r else None)

# 3. ids de fondos: opciones del select de fondos (si traen id) ---------------------------------------------------
cands = max(selects.values(), key=len) if selects else []
log(f'\n== 3. Select más largo: {len(cands)} opciones; numéricas: {sum(v.isdigit() for v, _ in cands)}')
fondos = [(v, t) for v, t in cands if v.isdigit()]
json.dump(fondos, open(f'{OUT}/fondos_ids.json', 'w'), ensure_ascii=False)


def bajar(fid, nombre, guardar=False):
    url = f'{BASE}/DetallesFCI/{fid}'
    st, b, dt, u = get(url)
    h = b.decode('utf-8', 'ignore')
    if guardar:
        open(f'{OUT}/detalle_{fid}.html', 'w').write(h)
    r = parse_detalle(h) if st == 200 else None
    if not r:
        log(f'  {fid} {nombre[:45]:45} {st} {dt:.1f}s sin cartera')
        return dict(id=fid, nombre=nombre, status=st, seg=round(dt, 2), cartera=False)
    cab, filas = r
    esp, rub, suma, total, pn = resumen(filas)
    log(f'  {fid} {nombre[:45]:45} {st} {dt:.1f}s al {cab.get("Cartera al")} | {len(esp)} especies, suma {suma} | '
        f'total {total} | PN {pn} | {cab.get("Clasificación")} {cab.get("Moneda")}')
    return dict(id=fid, nombre=nombre, status=st, seg=round(dt, 2), cartera=True, cab=cab, especies=esp, rubros=rub,
                suma=suma, total=total, pn=pn)


res = {}
log('\n== 4. Fondos de IOL')
iol = [(v, t) for v, t in fondos if re.search(r'\bIOL\b', t, re.I)]
log('  encontrados:', iol)
for v, t in iol:
    res[v] = bajar(v, t, guardar=True); time.sleep(1)

log('\n== 5. Muestra aleatoria (30) — tiempos, bloqueos, frescura')
random.seed(1)
for v, t in random.sample([x for x in fondos if x not in iol], min(30, max(0, len(fondos) - len(iol)))):
    res[v] = bajar(v, t); time.sleep(1)
ok = [r for r in res.values() if r.get('cartera')]
if res:
    log(f'  con cartera {len(ok)}/{len(res)} | seg medio {sum(r["seg"] for r in res.values()) / len(res):.2f} | '
        f'estados {sorted(set(str(r["status"]) for r in res.values()))}')
    log('  fechas "Cartera al":', sorted({r['cab'].get('Cartera al') for r in ok}, key=str))
json.dump(res, open(f'{OUT}/carteras.json', 'w'), ensure_ascii=False, indent=1)

# 6. sitio nuevo de CAFCI ------------------------------------------------------------------------------------------
log('\n== 6. CAFCI estadísticas')
st, b, dt, u = get('https://estadisticas.cafci.org.ar/fondos/1570?clase=5100')
h = b.decode('utf-8', 'ignore')
open(f'{OUT}/cafci_ficha.html', 'w').write(h)
log(st, f'{len(b):,} B {dt:.1f}s')
js = sorted(set(re.findall(r'<script[^>]+src="([^"]+)"', h)))
log('  scripts:', js[:10])
apis = set(re.findall(r'https?://[a-z0-9.\-]*cafci[a-z0-9.\-]*/[^\s"\'<>)]*', h))
for s in js[:8]:
    st2, b2, _, _ = get(urllib.parse.urljoin('https://estadisticas.cafci.org.ar/', s))
    j = b2.decode('utf-8', 'ignore')
    apis |= set(re.findall(r'https?://[a-z0-9.\-]*cafci[a-z0-9.\-]*/[^\s"\'`<>)]*', j))
    for m in re.finditer(r'.{0,80}(cartera|composicion)[^"\'`]{0,80}', j, re.I):
        log('   js', s[-30:], ':', m.group(0)[:200]); break
    for m in re.finditer(r'["\'`](/(?:api|v\d)/[^"\'`]{3,80})["\'`]', j):
        apis.add(m.group(1))
log('  urls/rutas API vistas:', sorted(apis)[:40])
for cand in ('https://api.pub.cafci.org.ar/fondo/1570/clase/5100/ficha', 'https://api.cafci.org.ar/fondo/1570/clase/5100/ficha',
             'https://estadisticas.cafci.org.ar/api/fondos/1570?clase=5100'):
    st, b, dt, u = get(cand, headers={'Origin': 'https://estadisticas.cafci.org.ar', 'Referer': 'https://estadisticas.cafci.org.ar/'})
    log(' ', st, f'{len(b):,} B', cand, b[:160])
log('\nfin')
