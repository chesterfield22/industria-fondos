"""Compara nuestras métricas con un informe diario 1816 (PDF): tablas 'Suscripciones netas y AUM por tipo de fondo'
y 'Rendimientos en pesos por tipo de fondo'.
Uso: python validar_1816.py informe.pdf data/base salida.csv [fecha]
La fecha se toma del PDF ('al d/m/aaaa') salvo que se indique. Para aislar diferencias de tipo de cambio, el AUM y los
flujos en USD se convierten con el tipo de cambio implícito del informe (Total - en ARS) / en USD.
"""
import re, subprocess, sys, os
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(__file__))
from metrics import compute, ORDEN

ALIAS = {s.lower(): s for s in ORDEN}
ALIAS.update({'total': 'Total'})


def num(x):
    x = x.replace('%', '').replace('.', '').replace(',', '.')
    try:
        return float(x)
    except ValueError:
        return np.nan


def find_page(pdf, pat):
    n = int(re.search(r'Pages:\s+(\d+)', subprocess.run(['pdfinfo', pdf], capture_output=True, text=True).stdout).group(1))
    for p in range(1, n + 1):
        t = subprocess.run(['pdftotext', '-q', '-layout', '-f', str(p), '-l', str(p), pdf, '-'], capture_output=True, text=True).stdout
        if re.search(pat, t):
            return t
    return ''


def parse_table(txt, ncols):
    """Filas con ncols números al final; el subtipo se identifica buscando su nombre en la etiqueta.
    'Total' sin subtipo: primera aparición = industria."""
    rows, seen_total = [], False
    names = sorted(ORDEN, key=len, reverse=True)
    for l in txt.splitlines():
        parts = re.split(r'\s{2,}', l.strip())
        if len(parts) < ncols + 1:
            continue
        nums = parts[-ncols:]
        if sum(bool(re.match(r'^-?[\d\.]*,?\d+%?$', x)) for x in nums) < ncols - 2:
            continue
        label = ' '.join(parts[:-ncols])
        sub = next((n for n in names if n.lower() in label.lower()), None)
        if sub is None:
            if 'Total' in label and not seen_total:
                sub, seen_total = 'Total', True
            else:
                continue
        rows.append([sub] + [num(x) for x in nums])
    return rows


def main(pdf, base_dir, out_csv, fecha=None):
    t13 = find_page(pdf, r'SUSCRIPCIONES NETAS Y AUM POR TIPO DE FONDO')
    t12 = find_page(pdf, r'RENDIMIENTOS EN PESOS POR TIPO DE FONDO')
    f = fecha or re.search(r'al (\d{1,2}/\d{1,2}/\d{4})', t13).group(1)
    T = pd.to_datetime(f, dayfirst=True)
    a = pd.DataFrame(parse_table(t13, 13), columns=['subtipo', 'aum_ars', 'aum_usd', 'aum_total', 'pct', 's1d', 's7d', 'sMTD', 's30d', 's90d', 'sYTD', 's365d', 'pYTD', 'p365d']).drop_duplicates('subtipo')
    r = pd.DataFrame(parse_table(t12, 7), columns=['subtipo', 'r1d', 'r7d', 'rMTD', 'r30d', 'r90d', 'rYTD', 'r365d']).drop_duplicates('subtipo')
    t0 = a[a.subtipo == 'Total'].iloc[0]
    fx16 = (t0.aum_total - t0.aum_ars) / t0.aum_usd
    res = compute(base_dir, os.path.dirname(out_csv) or '.', str(T.date()))
    S = res['seg'].copy()
    # reconvertir USD al tipo de cambio del informe para aislar el efecto FX en AUM
    S['aum_total_fx16'] = S.aum_ars_m + S.aum_usd_m * fx16
    S = S[(S.subtipo != 'Total') | (S.tipo == 'Total')]
    m = a.merge(r, on='subtipo', how='outer').merge(S, on='subtipo', how='left')
    m['o'] = m.subtipo.map({x: i for i, x in enumerate(['Total'] + ORDEN)}); m = m.sort_values('o')
    out = pd.DataFrame({'subtipo': m.subtipo,
                        'AUM_1816': m.aum_total, 'AUM_nuestro_fx1816': m.aum_total_fx16, 'AUM_dif%': m.aum_total_fx16 / m.aum_total - 1,
                        'AUM_USD_1816': m.aum_usd, 'AUM_USD_nuestro': m.aum_usd_m,
                        'susc1d_1816': m.s1d, 'susc1d_nuestro': m.susc_1d_m, 'susc30d_1816': m.s30d, 'susc30d_nuestro': m.susc_30d_m,
                        'suscYTD_1816': m.sYTD, 'suscYTD_nuestro': m.susc_YTD_m,
                        'r30d_1816%': m.r30d, 'r30d_nuestro%': m.rend_30d * 100, 'rYTD_1816%': m.rYTD, 'rYTD_nuestro%': m.rend_YTD * 100})
    out.to_csv(out_csv, index=False, float_format='%.4f')
    print('fecha', T.date(), '| FX implícito 1816:', round(fx16, 2), '| datos:', res['meta'])
    return out


if __name__ == '__main__':
    pd.set_option('display.width', 260)
    o = main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
    print(o.round(2).to_string())
