import re, urllib.request, urllib.error, urllib.parse, html as H_
H = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36', 'Accept': '*/*'}
def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=60) as r:
            b = r.read(); print(f'{r.status} {len(b):>9,}B {r.headers.get("Content-Type","")[:30]:30} {r.geturl()}')
            return b.decode('utf-8', 'ignore')
    except urllib.error.HTTPError as e: print(f'HTTP {e.code} {url}')
    except Exception as e: print(f'ERR {type(e).__name__} {url}')
    return ''
def text(h):
    h = re.sub(r'<(script|style)[^>]*>.*?</\1>', ' ', h, flags=re.S | re.I)
    return re.sub(r'\s+', ' ', H_.unescape(re.sub(r'<[^>]+>', ' ', h)))
for g in ('F6E483C6-2DEC-4A6E-A290-B4A522633F37', '6268295B-FED8-48AA-A3EE-1E7B20F5F0D8'):
    h = get('https://aif2.cnv.gov.ar/Presentations/publicview/' + g)
    t = text(h); i = t.find('Presentaci'); print('TEXT:', t[i:i + 1500])
    for u in sorted(set(re.findall(r'["\'](/[A-Za-z]+/[A-Za-z]+[^"\'\s]*)["\']', h)))[:60]:
        if not re.search(r'\.(css|js|png|svg|woff|ico)', u): print('  path:', u)
    for u in sorted(set(re.findall(r'https?://[^"\'\s<>]+', h)))[:30]:
        if not re.search(r'\.(css|js|png|svg|woff|ico)|googleapis|maps', u): print('  url:', u)
