import json, urllib.request, sys
sys.path.insert(0, 'pipeline')
import titulos as T
for t in ['T31Y7', 'TTCDO', 'AO27', 'BF47O', 'S26F7']:
    print(t, T.byma_ficha(t))
try:
    req = urllib.request.Request('https://api.invertironline.com/token', data=b'grant_type=password&username=x&password=y',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    urllib.request.urlopen(req, timeout=20)
except Exception as e:
    print('IOL token sin credenciales ->', type(e).__name__, getattr(e, 'code', ''), str(e)[:120])
