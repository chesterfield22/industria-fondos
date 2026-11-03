"""Extrae del informe diario 1816 (PDF) el mapeo cuota -> subtipo de fondo, con gerente y AUM.
Usa las paginas de 'Rendimientos diarios e historicos' de cada subtipo.
Uso: python parse_1816.py informe.pdf salida.csv
"""
import re, subprocess, sys, csv

SEG = [  # (patron del titulo, tipo, subtipo 1816)
    (r'MONEY MARKET ARS CL', 'Money Market', 'MM ARS Clásico'),
    (r'MONEY MARKET ARS DIN', 'Money Market', 'MM ARS Dinámico'),
    (r'MONEY MARKET USD CL', 'Money Market', 'MM USD Clásico'),
    (r'^T\+1', 'T+1', 'T+1'),
    (r'RENTA FIJA ARS CER', 'Renta Fija', 'RF ARS CER'),
    (r'RENTA FIJA ARS LECAP', 'Renta Fija', 'RF ARS Lecap'),
    (r'RENTA FIJA ARS DISCRECIONAL', 'Renta Fija', 'RF ARS Disc'),
    (r'RENTA FIJA MULTIMONEDA CORPORATIVO', 'Renta Fija', 'RF MULT Corp'),
    (r'RENTA FIJA MULTIMONEDA DISCRECIONAL', 'Renta Fija', 'RF MULT Disc'),
    (r'RENTA FIJA USD LINKED', 'Renta Fija', 'RF USD Linked'),
    (r'RENTA FIJA USD CORTO PLAZO', 'Renta Fija', 'RF USD CP'),
    (r'RENTA FIJA USD CORPORATIVO LOCAL', 'Renta Fija', 'RF USD corp loc'),
    (r'RENTA FIJA USD DISCRECIONAL', 'Renta Fija', 'RF USD Disc'),
    (r'RENTA FIJA USD GLOBAL Incl', 'Renta Fija', 'RF USD GL Inc ARG'),
    (r'RENTA FIJA USD GLOBAL Excl', 'Renta Fija', 'RF USD GL Ex ARG'),
    (r'RENTA VARIABLE BENCHMARK MERVAL', 'Renta Variable', 'RV ARG Merval'),
    (r'RENTA VARIABLE BENCHMARK ROFEX', 'Renta Variable', 'RV ARG Rofex 20'),
    (r'RENTA VARIABLE ARGENTINA OTROS', 'Renta Variable', 'RV ARG Otros'),
    (r'RENTA VARIABLE GLOBAL', 'Renta Variable', 'RV Global'),
    (r'RENTA MIXTA ARS', 'Renta Mixta', 'RM y Otros ARS'),
    (r'RENTA MIXTA USD', 'Renta Mixta', 'RM y Otros USD'),
    (r'^PYME', 'Destino Esp.', 'PYME'),
    (r'^INFRAESTRUCTURA', 'Destino Esp.', 'INFRA'),
    (r'^ASG', 'Destino Esp.', 'ASG'),
]
NUM = re.compile(r'^-?[\d\.]+(,\d+)?$')


def page_text(pdf, p):
    return subprocess.run(['pdftotext', '-q', '-layout', '-f', str(p), '-l', str(p), pdf, '-'], capture_output=True, text=True).stdout


def main(pdf, out):
    npages = int(re.search(r'Pages:\s+(\d+)', subprocess.run(['pdfinfo', pdf], capture_output=True, text=True).stdout).group(1))
    rows, pend = [], []
    for p in range(1, npages + 1):
        t = page_text(pdf, p)
        lines = [l for l in t.splitlines() if l.strip()]
        head = [l.strip() for l in lines[:6]]
        title = next((h for h in head if any(re.search(s[0], h) for s in SEG)), None)
        if not title or not any('Rendimientos diarios' in h for h in head):
            continue
        tipo, sub = next((s[1], s[2]) for s in SEG if re.search(s[0], title))
        for l in lines:
            parts = re.split(r'\s{2,}', l.strip())
            # fila de fondo: nombre, gerente, AUM cuota, AUM FCI, luego numeros
            if parts[0].startswith(('Promedio', 'Tamar', 'Cuota')):
                continue
            if len(parts) >= 6 and NUM.match(parts[2]) and NUM.match(parts[3]) and not NUM.match(parts[1]):
                rows.append(dict(pagina=p, tipo=tipo, subtipo=sub, cuota=parts[0], gerente=parts[1],
                                 aum_cuota_m=parts[2].replace('.', '').replace(',', '.'),
                                 aum_fci_m=parts[3].replace('.', '').replace(',', '.')))
            elif len(parts) >= 5 and NUM.match(parts[1]) and NUM.match(parts[2]):
                pend.append((p, tipo, sub, parts))   # nombre y gerente pegados (nombre truncado con '..')
    known = sorted({r['gerente'] for r in rows}, key=len, reverse=True)
    for p, tipo, sub, parts in pend:
        g = next((k for k in known if parts[0].endswith(' ' + k)), None)
        if not g:
            continue
        cuota = parts[0][: -len(g)].strip(' .')
        if not cuota:
            continue
        rows.append(dict(pagina=p, tipo=tipo, subtipo=sub, cuota=cuota, gerente=g,
                         aum_cuota_m=parts[1].replace('.', '').replace(',', '.'), aum_fci_m=parts[2].replace('.', '').replace(',', '.')))
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    print(len(rows), 'cuotas')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
