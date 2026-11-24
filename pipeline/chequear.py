"""Control previo (barato: una sola consulta al listado de la CNV) para no repetir corridas completas sin datos nuevos.

Se corre completa si: es manual, no hay corrida anterior legible, la última publicada no es de hoy (una corrida completa
por día como mínimo: tipo de cambio, MEP/CCL), el listado de la CNV no responde (se deja que el pipeline decida con su
respaldo) o la CNV listó una presentación que la última corrida publicada no procesó (versión nueva o revisada, o una
que había fallado). Si no, se saltean la descarga de ArgentinaDatos, el pipeline y la publicación.
Escribe run=si|no en $GITHUB_OUTPUT.
"""
import csv, json, os, sys
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))


def decidir(prev_dir, evento, dias):
    if evento != 'schedule':
        return 'si', f'corrida {evento}'
    try:
        info = json.load(open(os.path.join(prev_dir, 'base', 'cnv_info.json')))
        meta = dict(csv.reader(open(os.path.join(prev_dir, 'meta.csv'), encoding='utf-8')))
        gen = dt.datetime.strptime(meta['generado_utc'], '%Y-%m-%d %H:%M') - dt.timedelta(hours=3)
    except Exception as e:
        return 'si', f'sin corrida anterior legible ({type(e).__name__})'
    hoy = (dt.datetime.utcnow() - dt.timedelta(hours=3)).date()
    if gen.date() != hoy:
        return 'si', f'primera corrida del día (la última publicada es del {gen:%d/%m %H:%M} ART)'
    if not info.get('ok') or not info.get('firma'):
        return 'si', 'la última corrida no tomó la CNV'
    try:
        import cnv
        docs = cnv.listar().tail(dias)
    except Exception as e:
        return 'si', f'listado CNV no disponible ({type(e).__name__}): corrida completa con respaldo'
    nuevas = sorted(set(cnv.firma(docs).split(';')) - set(info['firma'].split(';')))
    if nuevas:
        return 'si', 'presentaciones nuevas en la CNV: ' + ', '.join(nuevas)
    return 'no', f'sin presentaciones nuevas en la CNV (última: {docs.fecha.iloc[-1].date()} recibida {docs.recibido.iloc[-1]}); no se procesa ni publica'


if __name__ == '__main__':
    run, motivo = decidir(sys.argv[1], os.environ.get('GITHUB_EVENT_NAME', 'manual'), int(os.environ.get('CNV_DIAS', 10)))
    print(f'correr={run}: {motivo}')
    if os.environ.get('GITHUB_OUTPUT'):
        open(os.environ['GITHUB_OUTPUT'], 'a').write(f'run={run}\n')
