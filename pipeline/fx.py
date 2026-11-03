"""Tipos de cambio diarios ARS/USD.

A3500 : BCRA, Comunicación A 3500 (api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD). Fuente primaria.
        Respaldo: serie 'mayorista' de ArgentinaDatos desplazada una rueda (registra el cierre del día hábil anterior;
        verificado igual a BCRA en las 26 ruedas 25/09-02/11/2026).
MEP   : 'bolsa' (venta) de ArgentinaDatos / dolarapi.
CCL   : 'contadoconliqui' (venta) de ArgentinaDatos / dolarapi.
Todas se llevan a calendario diario con último valor conocido (para fines de semana y feriados).
"""
import json, os, sys, datetime as dt
import pandas as pd
import urllib.request

BCRA = 'https://api.bcra.gob.ar/estadisticascambiarias/v1.0/Cotizaciones/USD?fechadesde={d}&fechahasta={h}'


def a3500_bcra(desde='2019-12-01', hasta=None):
    hasta = hasta or dt.date.today().isoformat()
    out = []
    d0 = pd.Timestamp(desde)
    while d0 <= pd.Timestamp(hasta):  # la API limita el rango: se pide por años
        d1 = min(d0 + pd.DateOffset(years=1) - pd.Timedelta(days=1), pd.Timestamp(hasta))
        url = BCRA.format(d=d0.date(), h=d1.date())
        req = urllib.request.Request(url, headers={'User-Agent': 'industria-fondos/1.0'})
        with urllib.request.urlopen(req, timeout=60) as r:
            js = json.load(r)
        for x in js.get('results', []):
            for det in x.get('detalle', []):
                if det.get('codigoMoneda') == 'USD':
                    out.append((x['fecha'], det['tipoCotizacion']))
        d0 = d1 + pd.Timedelta(days=1)
    s = pd.Series({pd.Timestamp(f): float(v) for f, v in out}).sort_index()
    return s[s > 0]


def ad_series(path, casa):
    d = pd.DataFrame(json.load(open(os.path.join(path, casa, 'index.json'))))
    d['fecha'] = pd.to_datetime(d.fecha)
    return d.drop_duplicates('fecha', keep='last').set_index('fecha').venta.astype(float).sort_index()


def a3500_respaldo(path):
    """mayorista registrado en la fecha d = A3500 del día hábil anterior -> se asigna al día hábil anterior."""
    m = ad_series(path, 'mayorista')
    m = m[m.ne(m.shift(1)) | (m.index.dayofweek < 5)]
    vals = m.shift(-1)  # el valor del próximo registro corresponde a este día
    bd = vals[vals.index.dayofweek < 5].dropna()
    return bd


def build(ad_dolares_path, out_csv, usar_bcra=True):
    src = 'BCRA'
    try:
        if not usar_bcra:
            raise RuntimeError('BCRA deshabilitado')
        a3500 = a3500_bcra()
    except Exception as e:  # respaldo documentado
        print('A3500: BCRA no disponible ->', e, '| se usa respaldo ArgentinaDatos mayorista desplazado')
        a3500 = a3500_respaldo(ad_dolares_path); src = 'ArgentinaDatos mayorista (desplazado 1 rueda)'
    mep = ad_series(ad_dolares_path, 'bolsa'); ccl = ad_series(ad_dolares_path, 'contadoconliqui')
    idx = pd.date_range('2019-12-01', max(a3500.index.max(), mep.index.max()), freq='D')
    fx = pd.DataFrame({'a3500': a3500, 'mep': mep, 'ccl': ccl}).reindex(idx).ffill()
    fx.index.name = 'fecha'
    fx = fx[fx.index >= '2020-01-01']
    fx.to_csv(out_csv, float_format='%.4f')
    open(os.path.join(os.path.dirname(out_csv), 'fx_fuente.txt'), 'w').write(src)
    print('FX', fx.index.min().date(), fx.index.max().date(), 'A3500 fuente:', src)
    return fx


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2], usar_bcra=(len(sys.argv) < 4 or sys.argv[3] != 'sin_bcra'))
