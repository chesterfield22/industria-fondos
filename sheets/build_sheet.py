"""Genera 'Industria Fondos.xlsx': plantilla de Google Sheets que lee los CSV publicados en la rama `data`.

No contiene datos: cada pestaña oculta '_xxx' tiene un IMPORTDATA(BASE & archivo, ",", "en_US") y las pestañas visibles
calculan con fórmulas de Google Sheets (FILTER, SORT, CHOOSECOLS, XLOOKUP, SCAN/MAP). Se sube a Drive con conversión a
Google Sheets. Las columnas se ubican por nombre de encabezado (MATCH), no por posición, para tolerar cambios de esquema.

Uso: python sheets/build_sheet.py salida.xlsx [BASE_URL]
"""
import os, sys
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.chart import LineChart, BarChart, Reference
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.formatting.rule import FormulaRule
from openpyxl.utils import get_column_letter as L

BASE = 'https://raw.githubusercontent.com/chesterfield22/industria-fondos/data/'
HZ = ['1d', '7d', 'MTD', '30d', '90d', 'YTD', '365d']
TIPOS = {'Money Market': ['MM ARS Clásico', 'MM ARS Dinámico', 'MM USD Clásico'], 'T+1': [],
         'Renta Fija': ['RF ARS Lecap', 'RF ARS CER', 'RF ARS Disc', 'RF MULT Corp', 'RF MULT Disc', 'RF USD Linked', 'RF USD CP',
                        'RF USD corp loc', 'RF USD Disc', 'RF USD GL Inc ARG', 'RF USD GL Ex ARG'],
         'Renta Variable': ['RV ARG Merval', 'RV ARG Rofex 20', 'RV ARG Otros', 'RV Global'],
         'Renta Mixta': ['RM y Otros ARS', 'RM y Otros USD'], 'Destino Esp.': ['PYME', 'INFRA', 'ASG'], 'Sin clasificar': []}
SUBTIPOS = [s for v in TIPOS.values() for s in v] + ['T+1']
SEGMENTOS = ['Total'] + list(TIPOS) + [s for v in TIPOS.values() for s in v]

INDIGO, MINT, LILA, LILA_CL, GRIS = '1E0069', '00FFB3', '7F71A5', 'ECE9F4', '6B6B7B'
F_TIT = Font(name='Arial', size=16, bold=True, color='FFFFFF')
F_SUB = Font(name='Arial', size=9, color='FFFFFF')
F_H = Font(name='Arial', size=9, bold=True, color='FFFFFF')
F_B = Font(name='Arial', size=9)
F_BB = Font(name='Arial', size=9, bold=True)
F_LAB = Font(name='Arial', size=9, bold=True, color=INDIGO)
F_NOTE = Font(name='Arial', size=8, italic=True, color=GRIS)
FILL_I = PatternFill('solid', fgColor=INDIGO)
FILL_M = PatternFill('solid', fgColor=MINT)
FILL_L = PatternFill('solid', fgColor=LILA)
FILL_LC = PatternFill('solid', fgColor=LILA_CL)
CENT = Alignment(horizontal='center', vertical='center', wrap_text=True)
thin = Side(style='thin', color='C9C4DB')
BOX = Border(bottom=thin)
NUM = '#,##0;[Red]-#,##0'
NUM1 = '#,##0.0;[Red]-#,##0.0'
NUM2 = '#,##0.00;[Red]-#,##0.00'
PCT = '0.00%;[Red]-0.00%'
PCT1 = '0.0%'
FECHA = 'dd/mm/yyyy'

wb = Workbook()


def col(tab, name):
    """Columna completa de una pestaña importada, ubicada por nombre de encabezado."""
    return f'INDEX({tab}!$A:$AY,0,MATCH("{name}",{tab}!$1:$1,0))'


def meta(k):
    return f'VLOOKUP("{k}",_meta!$A:$B,2,FALSE)'


def banda(ws, titulo, sub, ncols):
    ws.sheet_view.showGridLines = False
    for c in range(1, ncols + 1):
        ws.cell(1, c).fill = FILL_I; ws.cell(2, c).fill = FILL_I
    ws['A1'] = titulo; ws['A1'].font = F_TIT
    ws['A2'] = sub; ws['A2'].font = F_SUB
    ws.row_dimensions[1].height = 28


def header(ws, r, c0, labels, fill=FILL_I, h=30):
    for i, t in enumerate(labels):
        x = ws.cell(r, c0 + i, t); x.font = F_H; x.fill = fill; x.alignment = CENT
    ws.row_dimensions[r].height = h


def selector(ws, ref, label_ref, label, value, note=None):
    ws[label_ref] = label; ws[label_ref].font = F_LAB
    ws[ref] = value; ws[ref].fill = FILL_M; ws[ref].font = F_BB; ws[ref].alignment = Alignment(horizontal='left')
    if note:
        nr = ws[ref].offset(0, 1); nr.value = note; nr.font = F_NOTE


def lista(ws, ref, rango):
    dv = DataValidation(type='list', formula1=rango, allow_blank=False, showDropDown=False)
    ws.add_data_validation(dv); dv.add(ref)


def widths(ws, d):
    for k, v in d.items():
        ws.column_dimensions[k].width = v


def fmt(ws, rng, nf=None, font=F_B):
    for row in ws[rng]:
        for c in row:
            if nf: c.number_format = nf
            c.font = font


def colfmt(ws, letters, nf):
    """Formato a nivel columna (no crea celdas): liviano y sobrevive a la conversión."""
    for c in letters:
        ws.column_dimensions[c].number_format = nf
        ws.column_dimensions[c].font = F_B


def grid_rows(ws, n):
    """Asegura n filas en la grilla (los resultados matriciales no agregan filas solos)."""
    ws.cell(n, 1).font = F_B


def color_series(ch, colors, line=True, width=22000):
    for s, c in zip(ch.series, colors):
        s.graphicalProperties = GraphicalProperties()
        if line:
            s.graphicalProperties.line.solidFill = c; s.graphicalProperties.line.width = width; s.smooth = False
        else:
            s.graphicalProperties.solidFill = c; s.graphicalProperties.line.solidFill = c


# =============================================================== Resumen
R = wb.active; R.title = 'Resumen'
banda(R, 'Industria Fondos — FCI Argentina', None, 14)
R['A2'] = f'=IFERROR("Fecha de corte "&TEXT({meta("fecha_corte")},"dd/mm/yyyy")&"  ·  clases con dato: "&{meta("clases_con_dato")}&"  ·  AUM arrastrado: "&TEXT({meta("aum_arrastrado_pct")},"0.00%")&"  ·  actualizado (UTC) "&{meta("generado_utc")},"Cargando datos… si persiste, ver pestaña Notas (Permitir acceso)")'
R['A2'].font = F_SUB
selector(R, 'C4', 'A4', 'Tipo de cambio USD', 'A3500')
lista(R, 'C4', '"A3500,MEP,CCL"')
R['D4'] = '=IFERROR(VLOOKUP(LOWER(C4),_meta!$A:$B,2,FALSE),"")'; R['D4'].number_format = NUM2; R['D4'].font = F_BB
R['E4'] = 'ARS/USD — se aplica al AUM de clases en USD (Resumen y Segmentos). Flujos y rendimientos en pesos: A3500 diario.'; R['E4'].font = F_NOTE
for i, (k, lab) in enumerate((('a3500', 'A3500'), ('mep', 'MEP'), ('ccl', 'CCL'))):
    R.cell(5, 3 + 2 * i, lab).font = F_LAB
    x = R.cell(5, 4 + 2 * i, f'=IFERROR({meta(k)},"")'); x.number_format = NUM2; x.font = F_B
R['A5'] = 'Cotizaciones a la fecha de corte'; R['A5'].font = F_NOTE
# KPIs
kpis = [('AUM total (ARS M)', '=D19'), ('AUM clases ARS (ARS M)', '=B19'), ('AUM clases USD (USD M)', '=C19'),
        ('Susc. netas 1d (ARS M)', '=F19'), ('Susc. netas MTD (ARS M)', '=H19'), ('Susc. netas YTD (ARS M)', '=J19')]
for i, (lab, f) in enumerate(kpis):
    c = 1 + 2 * i
    R.merge_cells(start_row=7, start_column=c, end_row=7, end_column=c + 1)
    R.merge_cells(start_row=8, start_column=c, end_row=8, end_column=c + 1)
    a = R.cell(7, c, lab); a.font = F_H; a.fill = FILL_L; a.alignment = CENT
    R.cell(7, c + 1).fill = FILL_L
    b = R.cell(8, c, f); b.font = Font(name='Arial', size=14, bold=True, color=INDIGO); b.alignment = CENT; b.number_format = NUM
R.row_dimensions[8].height = 26
# tabla por tipo
R['A10'] = 'Por tipo de fondo'; R['A10'].font = Font(name='Arial', size=11, bold=True, color=INDIGO)
hdr = ['Tipo', 'AUM clases ARS\n(ARS M)', 'AUM clases USD\n(USD M)', 'AUM total\n(ARS M, TC elegido)', '% AUM',
       'Susc. 1d', 'Susc. 7d', 'Susc. MTD', 'Susc. 30d', 'Susc. YTD', 'Susc. YTD\n% AUM inicial', 'Rend. 30d\n(en pesos)',
       'Rend. YTD\n(en pesos)', 'Rend. 365d\n(en pesos)']
header(R, 11, 1, hdr, h=40)
filas = list(TIPOS) + ['Total']
campos = {2: 'aum_ars_m', 3: 'aum_usd_m', 6: 'susc_1d_m', 7: 'susc_7d_m', 8: 'susc_MTD_m', 9: 'susc_30d_m', 10: 'susc_YTD_m',
          11: 'susc_YTD_pct_aum', 12: 'rend_30d', 13: 'rend_YTD', 14: 'rend_365d'}
for i, t in enumerate(filas):
    r = 12 + i
    R.cell(r, 1, t).font = F_BB if t == 'Total' else F_B
    R.cell(r, 26, f'=IFERROR(MATCH("{t}|Total",_seg!$AZ:$AZ,0),"")')
    for c, name in campos.items():
        x = R.cell(r, c, f'=IFERROR(INDEX(_seg!$A:$AY,$Z{r},MATCH("{name}",_seg!$1:$1,0)),"")')
        x.number_format = PCT if ('rend' in name or 'pct' in name) else NUM
        x.font = F_BB if t == 'Total' else F_B
    x = R.cell(r, 4, f'=IFERROR(B{r}+C{r}*$D$4,"")'); x.number_format = NUM; x.font = F_BB
    x = R.cell(r, 5, f'=IFERROR(D{r}/$D$19,"")'); x.number_format = PCT1; x.font = F_BB if t == 'Total' else F_B
    for c in range(1, 15):
        R.cell(r, c).border = BOX
        if t == 'Total': R.cell(r, c).fill = FILL_LC
R.column_dimensions['Z'].hidden = True
R['A20'] = ('Suscripciones netas = AUM_t − AUM_t-1 × VCP_t / VCP_t-1 por clase y día (clases USD a pesos al A3500 de cada día). '
            'Rendimientos: promedio ponderado por AUM, netos de honorarios, en pesos. % AUM inicial = susc. / AUM al cierre del año anterior.')
R['A20'].font = F_NOTE
widths(R, {'A': 17, **{L(i): 13 for i in range(2, 15)}})
R.freeze_panes = 'A3'

# =============================================================== Segmentos
S = wb.create_sheet('Segmentos')
banda(S, 'Segmentos (esquema 1816)', None, 22)
S['A2'] = f'=IFERROR("Fecha de corte "&TEXT({meta("fecha_corte")},"dd/mm/yyyy")&"  ·  AUM en ARS M; clases USD al "&Resumen!C4&" "&TEXT(Resumen!D4,"0.00")&" (cambiar en Resumen)  ·  rendimientos en pesos, netos","")'
S['A2'].font = F_SUB
grp = [(2, 6, 'AUM'), (7, 14, 'Suscripciones netas (ARS M)'), (15, 21, 'Rendimiento en pesos')]
for c0, c1, t in grp:
    S.merge_cells(start_row=4, start_column=c0, end_row=4, end_column=c1)
    x = S.cell(4, c0, t); x.font = F_H; x.fill = FILL_L; x.alignment = CENT
    for c in range(c0, c1 + 1): S.cell(4, c).fill = FILL_L
hdr = ['Segmento', 'Clases ARS\n(ARS M)', 'Clases USD\n(USD M)', 'Total\n(ARS M)', '% total', 'N° fondos'] + HZ + ['YTD % AUM'] + HZ + ['% AUM\narrastrado']
header(S, 5, 1, hdr, h=34)
seg_campos = {2: 'aum_ars_m', 3: 'aum_usd_m', 6: 'n_fondos', **{7 + i: f'susc_{h}_m' for i, h in enumerate(HZ)}, 14: 'susc_YTD_pct_aum',
              **{15 + i: f'rend_{h}' for i, h in enumerate(HZ)}, 22: 'aum_stale_pct'}
rows = [('Total', 'Total', 'Total INDUSTRIA')]
for t, subs in TIPOS.items():
    rows.append((t, 'Total', t))
    rows += [(t, s, '    ' + s) for s in subs]
r = 6
for t, s, lab in rows:
    bold = s == 'Total'
    S.cell(r, 1, lab).font = F_BB if bold else F_B
    S.cell(r, 26, f'=IFERROR(MATCH("{t}|{s}",_seg!$AZ:$AZ,0),"")')
    for c, name in seg_campos.items():
        x = S.cell(r, c, f'=IFERROR(INDEX(_seg!$A:$AY,$Z{r},MATCH("{name}",_seg!$1:$1,0)),"")')
        x.number_format = PCT if ('rend' in name or 'pct' in name) else NUM
        x.font = F_BB if bold else F_B
    x = S.cell(r, 4, f'=IFERROR(B{r}+C{r}*Resumen!$D$4,"")'); x.number_format = NUM; x.font = F_BB
    x = S.cell(r, 5, f'=IFERROR(D{r}/$D$6,"")'); x.number_format = PCT1; x.font = F_BB if bold else F_B
    for c in range(1, 23):
        S.cell(r, c).border = BOX
        if bold: S.cell(r, c).fill = FILL_LC
    r += 1
S.column_dimensions['Z'].hidden = True
S.cell(r + 1, 1, 'Subtipos: clasificación del informe 1816 (≈97% del AUM), regla por categoría CAFCI y, para el resto, el subtipo con '
                 'rendimiento diario más parecido (120 ruedas). Segmentos de USD: rendimiento en pesos incluye la variación del A3500.').font = F_NOTE
widths(S, {'A': 24, **{L(i): 11 for i in range(2, 23)}})
S.freeze_panes = 'B6'

# =============================================================== Gerentes
G = wb.create_sheet('Gerentes')
banda(G, 'Sociedades gerentes', None, 16)
G['A2'] = f'=IFERROR("Fecha de corte "&TEXT({meta("fecha_corte")},"dd/mm/yyyy")&"  ·  AUM en ARS M (clases USD al A3500)  ·  share = participación en el segmento elegido","")'
G['A2'].font = F_SUB
selector(G, 'C4', 'A4', 'Segmento', 'Total', 'Total, tipo o subtipo 1816')
lista(G, 'C4', f"_cfg!$A$2:$A${1 + len(SEGMENTOS)}")
selector(G, 'C5', 'A5', 'Tipo de gerente', 'Todas')
lista(G, 'C5', '"Todas,Bancaria,Independiente"')
selector(G, 'C6', 'A6', 'Destacar gerente', 'Adcap')
lista(G, 'C6', "_ger!$A$2:$A$200")
header(G, 8, 1, ['#', 'Gerente', 'Tipo', 'AUM en segmento\n(ARS M)', 'Share', 'Susc. 1d\n(ARS M)', 'Susc. 30d\n(ARS M)',
                 'Susc. YTD\n(ARS M)', 'N° fondos'], h=34)
M = lambda n, t='_gs': f'MATCH("{n}",{t}!$1:$1,0)'
GD = '_gs!$A$2:$Z'
G['B9'] = ('=IFERROR(SORT(CHOOSECOLS(FILTER(' + GD + f',INDEX({GD},0,{M("segmento")})=$C$4,'
           f'(($C$5="Todas")+(INDEX({GD},0,{M("tipo_gerente")})=$C$5))>0),'
           + ','.join(M(n) for n in ('gerente', 'tipo_gerente', 'aum_m', 'share', 'susc_1d_m', 'susc_30d_m', 'susc_YTD_m', 'n_fondos'))
           + '),3,FALSE),"Sin datos para ese filtro")')
G['A9'] = '=ARRAYFORMULA(IF(ISNUMBER(D9:D300),ROW(D9:D300)-ROW(D9)+1,))'
for c, nf in zip('ABCDEFGHI', ['0', '@', '@', NUM, PCT, NUM, NUM, NUM, '0']):
    colfmt(G, c, nf)
G.conditional_formatting.add('A9:I300', FormulaRule(formula=['AND($B9<>"",$B9=$C$6)'], fill=FILL_M, font=Font(bold=True)))
# bancarias vs independientes (en el segmento elegido)
G['K8'] = 'Bancarias vs independientes'; G['K8'].font = F_LAB
header(G, 9, 11, ['Tipo', 'AUM (ARS M)', 'Share', 'Susc. 30d', 'Susc. YTD'], h=30)
for i, tg in enumerate(['Bancaria', 'Independiente', 'Sin dato']):
    r = 10 + i
    G.cell(r, 11, tg).font = F_B
    for c, name in ((12, 'aum_m'), (14, 'susc_30d_m'), (15, 'susc_YTD_m')):
        x = G.cell(r, c, f'=IFERROR(SUMIFS({col("_gs", name)},{col("_gs", "segmento")},$C$4,{col("_gs", "tipo_gerente")},$K{r}),"")')
        x.number_format = NUM; x.font = F_B
    x = G.cell(r, 13, f'=IFERROR(L{r}/SUM($L$10:$L$12),"")'); x.number_format = PCT1; x.font = F_B
ch = BarChart(); ch.type = 'bar'; ch.style = 10; ch.title = 'Top 15 por AUM en el segmento (ARS M)'
ch.add_data(Reference(G, min_col=4, min_row=9, max_row=23), titles_from_data=False)
ch.set_categories(Reference(G, min_col=2, min_row=9, max_row=23))
ch.legend = None; ch.y_axis.majorGridlines = None; ch.x_axis.scaling.orientation = 'maxMin'
color_series(ch, [INDIGO], line=False)
ch.height = 9; ch.width = 15
G.add_chart(ch, 'K15')
widths(G, {'A': 5, 'B': 30, 'C': 14, 'D': 15, 'E': 9, 'F': 12, 'G': 12, 'H': 13, 'I': 9, 'J': 3, 'K': 16, 'L': 14, 'M': 9, 'N': 12, 'O': 12})
G.freeze_panes = 'A9'

# =============================================================== Ranking
K = wb.create_sheet('Ranking')
banda(K, 'Ranking de fondos por subtipo', None, 19)
K['A2'] = f'=IFERROR("Fecha de corte "&TEXT({meta("fecha_corte")},"dd/mm/yyyy")&"  ·  clases de fondos con AUM ≥ el mínimo  ·  rendimientos en moneda de la clase, netos de honorarios","")'
K['A2'].font = F_SUB
selector(K, 'C4', 'A4', 'Subtipo', 'T+1')
lista(K, 'C4', f"_cfg!$B$2:$B${1 + len(SUBTIPOS)}")
selector(K, 'C5', 'A5', 'Ordenar por', '30d', 'horizonte del rendimiento')
lista(K, 'C5', '"' + ','.join(HZ) + '"')
selector(K, 'C6', 'A6', 'AUM mínimo del fondo (ARS M)', 1000, 'el informe 1816 usa 1.000')
selector(K, 'H4', 'F4', 'Destacar gerente', 'Adcap')
lista(K, 'H4', "_ger!$A$2:$A$200")
K['F5'] = '=IFERROR("Clases en el ranking: "&COUNT(F10:F),"")'; K['F5'].font = F_NOTE
K.merge_cells('H8:N8'); K['H8'] = 'Rendimiento (moneda de la clase)'
for c in range(8, 15): K.cell(8, c).fill = FILL_L
K['H8'].font = F_H; K['H8'].alignment = CENT
header(K, 9, 1, ['Pos', 'Clase', 'Gerente', 'Tipo gerente', 'Mon.', 'AUM clase\n(ARS M)', 'AUM fondo\n(ARS M)'] + HZ +
       ['Vol. 90d\nanualiz.', 'Rend/Vol\n90d', 'Hon. G+D\n(% anual)', 'Último dato'], h=34)
RD = '_rank!$A$2:$AZ'
KK = 'MATCH("r_"&$C$5,_rank!$1:$1,0)'
K['B10'] = ('=IFERROR(CHOOSECOLS(SORT(FILTER(' + RD + f',INDEX({RD},0,{M("subtipo", "_rank")})=$C$4,ISNUMBER(INDEX({RD},0,{KK})),'
            f'INDEX({RD},0,{M("aum_fondo_m", "_rank")})>=$C$6),{KK},FALSE),'
            + ','.join(M(n, '_rank') for n in ['clase', 'gerente', 'tipo_gerente', 'moneda', 'aum_clase_m', 'aum_fondo_m'] +
                       [f'r_{h}' for h in HZ] + ['vol_90d', 'rend_vol', 'hon_gd', 'fecha_dato'])
            + '),"Sin clases para ese filtro")')
K['A10'] = '=ARRAYFORMULA(IF(ISNUMBER(F10:F600),ROW(F10:F600)-ROW(F10)+1,))'
for c, nf in zip([L(i) for i in range(1, 19)], ['0', '@', '@', '@', '@', NUM, NUM] + [PCT] * 7 + [PCT, '0.00', '0.00', FECHA]):
    colfmt(K, c, nf)
K.conditional_formatting.add('A10:R600', FormulaRule(formula=['AND($C10<>"",$C10=$H$4)'], fill=FILL_M, font=Font(bold=True)))
K.conditional_formatting.add('H9:N9', FormulaRule(formula=['H$9=$C$5'], fill=FILL_M, font=Font(bold=True, color=INDIGO)))
widths(K, {'A': 5, 'B': 40, 'C': 16, 'D': 12, 'E': 6, 'F': 11, 'G': 11, **{L(i): 9 for i in range(8, 15)}, 'O': 9, 'P': 9, 'Q': 9, 'R': 11})
K.freeze_panes = 'C10'

# =============================================================== Fondo
F = wb.create_sheet('Fondo')
banda(F, 'Fondo: cuotaparte, AUM y suscripciones', None, 22)
F['A2'] = '=IFERROR("Historia diaria por clase (fuente: planilla diaria CNV). Base 100 y AUM en la moneda de cada clase.","")'
F['A2'].font = F_SUB
selector(F, 'C4', 'A4', 'Clase A', 'IOL Cash Management - Clase A')
lista(F, 'C4', "_cls!$B$2:$B$3000")
selector(F, 'C5', 'A5', 'Clase B (comparar)', 'Fima Ahorro Pesos - Clase A', 'opcional')
lista(F, 'C5', "_cls!$B$2:$B$3000")
selector(F, 'C7', 'A7', 'Hasta', f'=IFERROR({meta("fecha_corte")},TODAY())')
selector(F, 'C6', 'A6', 'Desde', '=IFERROR(EDATE(C7,-12),"")', 'fechas editables')
for ref in ('C6', 'C7'):
    F[ref].number_format = FECHA
info = [('ID clase', 'clase_id'), ('Fondo', 'fondo'), ('Gerente', 'gerente'), ('Tipo gerente', 'tipo_gerente'), ('Tipo', 'tipo'),
        ('Subtipo', 'subtipo'), ('Moneda', 'moneda'), ('AUM (moneda clase)', 'aum'), ('AUM (ARS M)', 'aum_ars_m'), ('Último dato', 'fecha_dato')]
header(F, 9, 1, ['Dato', 'Clase A', 'Clase B'], h=20)
for i, (lab, name) in enumerate(info):
    r = 10 + i
    F.cell(r, 1, lab).font = F_LAB
    for c, sel in ((2, '$C$4'), (3, '$C$5')):
        x = F.cell(r, c, f'=IF({sel}="","",IFERROR(XLOOKUP({sel},{col("_cls", "clase")},{col("_cls", name)}),"no encontrada"))')
        x.font = F_B
        x.number_format = NUM if name in ('aum', 'aum_ars_m') else (FECHA if name == 'fecha_dato' else 'General')
        x.alignment = Alignment(horizontal='left')
r = 10 + len(info)
calc = [('Posición 30d en subtipo', 'pos'), ('Rend. período', 'rp'), ('Rend. período anualizado (TEA)', 'ra'),
        ('Volatilidad diaria anualizada', 'vol'), ('Máx. caída (drawdown) período', 'dd'), ('Susc. netas período (moneda clase, M)', 'sus'),
        ('Ruedas en el período', 'n')]
cA = {'fe': '_fd!$A$2:$A', 'v': '_fd!$B$2:$B', 's': '_fd!$D$2:$D', 'r': '_fd!$E$2:$E', 'dd': '_fd!$R$2:$R'}
cB = {'fe': '_fd!$G$2:$G', 'v': '_fd!$H$2:$H', 's': '_fd!$J$2:$J', 'r': '_fd!$K$2:$K', 'dd': '_fd!$T$2:$T'}
for j, (lab, k) in enumerate(calc):
    rr = r + j
    F.cell(rr, 1, lab).font = F_LAB
    for c, cc, idc in ((2, cA, 'B10'), (3, cB, 'C10')):
        if k == 'pos':
            f = (f'=IFERROR(XLOOKUP({idc},{col("_rank", "clase_id")},{col("_rank", "pos_30d")})&" de "&'
                 f'COUNTIFS({col("_rank", "subtipo")},{L(c)}15,{col("_rank", "r_30d")},"<>"),"s/d (fondo < 1.000 M)")')
        elif k == 'rp':
            f = f'=IFERROR(INDEX(FILTER({cc["v"]},{cc["v"]}<>""),COUNT({cc["v"]}))/INDEX({cc["v"]},1)-1,"")'
        elif k == 'ra':
            f = (f'=IFERROR((1+{L(c)}{r + 1})^(365/(MAX({cc["fe"]})-INDEX({cc["fe"]},1)))-1,"")')
        elif k == 'vol':
            f = f'=IFERROR(STDEV(FILTER({cc["r"]},ISNUMBER({cc["r"]})))*SQRT(252),"")'
        elif k == 'dd':
            f = f'=IFERROR(MIN({cc["dd"]}),"")'
        elif k == 'sus':
            f = f'=IFERROR(SUM({cc["s"]})/1000000,"")'
        else:
            f = f'=COUNT({cc["v"]})'
        x = F.cell(rr, c, f); x.font = F_BB if k in ('rp', 'ra') else F_B
        x.number_format = PCT if k in ('rp', 'ra', 'vol', 'dd') else (NUM1 if k == 'sus' else 'General')
        x.alignment = Alignment(horizontal='left')
F.cell(r + len(calc) + 1, 1, 'Rendimientos desde el último VCP ≤ "Desde" hasta el último ≤ "Hasta". Volatilidad: desvío de rend. diarios × √252. '
                              'Si un fondo tuvo un rebase de cuotaparte (salto > 40%), no se mide rendimiento a través de él.').font = F_NOTE
# tabla diaria clase A
F['T8'] = 'Serie diaria — Clase A (más reciente arriba)'; F['T8'].font = F_LAB
header(F, 9, 20, ['Fecha', 'VCP', 'AUM', 'Susc. netas', 'Rend. diario'], h=20)
F['T10'] = '=IFERROR(SORT(FILTER(_fd!$A$2:$E,_fd!$A$2:$A<>""),1,FALSE),"")'
for c, nf in zip('TUVWX', [FECHA, '#,##0.000000', NUM, NUM, '0.0000%;[Red]-0.0000%']):
    colfmt(F, c, nf)
grid_rows(F, 4000)
widths(F, {'A': 30, 'B': 34, 'C': 34, 'D': 2, **{L(i): 9 for i in range(5, 19)}, 'S': 2, 'T': 11, 'U': 13, 'V': 17, 'W': 15, 'X': 11})
F.freeze_panes = 'A9'

# =============================================================== Notas
N = wb.create_sheet('Notas')
banda(N, 'Notas, metodología y fuentes', None, 3)
N['A2'] = '=IFERROR("Fecha de corte "&TEXT(' + meta('fecha_corte') + ',"dd/mm/yyyy")&"  ·  última fecha en la fuente "&TEXT(' + meta('ultima_fecha_datos') + ',"dd/mm/yyyy"),"")'
N['A2'].font = F_SUB
notas = [
    ('Primera apertura', 'Google pide autorización para traer datos externos: hacé clic en cualquier celda con #REF! y elegí "Permitir acceso". '
                         'Se hace una vez por planilla.'),
    ('Actualización', 'Un proceso automático (GitHub Actions) reconstruye la base todos los días hábiles (~22:30 y ~10:00) y publica los CSV. '
                      'Google refresca IMPORTDATA aprox. cada hora; no hace falta tocar nada.'),
    ('Fecha de corte', 'Última fecha hábil con ≥ 97% de las clases reportadas en la planilla CNV. Las clases que no reportaron ese día '
                       'arrastran su último dato (máx. 7 días) y se informa el % de AUM arrastrado.'),
    ('Suscripciones netas', 'Por clase y día: AUM_t − AUM_t−1 × VCP_t / VCP_t−1. Se suman clases y días. Clases en USD: a pesos al A3500 de cada día.'),
    ('Rendimientos', 'VCP_T / VCP_base − 1, base = último VCP ≤ T − h días corridos (MTD: último del mes anterior; YTD: último del año anterior). '
                     'Netos de honorarios. Segmentos: promedio ponderado por AUM. Verificado contra el informe 1816 del 02/11/2026.'),
    ('Tipo de cambio', 'A3500 (BCRA, Com. A 3500) por defecto; MEP y CCL (venta) como alternativas para el AUM. El informe 1816 usa un tipo de cambio propio.'),
    ('Segmentación', 'Subtipos 1816: del propio informe (≈97% del AUM), regla por categoría CAFCI y, para el resto, el subtipo cuyo rendimiento '
                     'diario (120 ruedas) más se parece. Correcciones manuales: archivo ref/segmento_manual.csv del repositorio.'),
    ('Calidad de datos', 'Se eliminan picos transitorios de VCP (>40%) y AUM (×5) que revierten en ≤ 5 ruedas; saltos permanentes de VCP > 40% son '
                         'rebases (no se mide rendimiento ni flujo a través de ellos); tramos de AUM informados en otra moneda se normalizan.'),
    ('Unidades', 'AUM y suscripciones en millones (ARS M o USD M). Rendimientos en % efectivo del período.'),
    ('Fuentes', 'CNV — planilla diaria de cuotapartes (vía ArgentinaDatos); BCRA — A3500; ArgentinaDatos/dolarapi — MEP y CCL; '
                '1816 — segmentación y tipo de gerente.'),
]
header(N, 4, 1, ['Tema', 'Detalle'], h=20)
for i, (a, b) in enumerate(notas):
    N.cell(5 + i, 1, a).font = F_LAB
    x = N.cell(5 + i, 2, b); x.font = F_B; x.alignment = Alignment(wrap_text=True, vertical='top')
    N.row_dimensions[5 + i].height = 40
r = 6 + len(notas)
N.cell(r, 1, 'Datos (URL base)').font = F_LAB
N.cell(r, 2, '=_cfg!$B$1').font = F_B
N.cell(r + 1, 1, 'Fuente A3500 hoy').font = F_LAB
N.cell(r + 1, 2, f'=IFERROR({meta("fuente_a3500")},"")').font = F_B
N.cell(r + 2, 1, 'Generado (UTC)').font = F_LAB
N.cell(r + 2, 2, f'=IFERROR({meta("generado_utc")},"")').font = F_B
widths(N, {'A': 22, 'B': 120})

# =============================================================== ocultas: config, datos importados, cálculos
C = wb.create_sheet('_cfg')
C['A1'] = 'URL base'; C['B1'] = BASE
for i, s in enumerate(SEGMENTOS): C.cell(2 + i, 1, s)
for i, s in enumerate(SUBTIPOS): C.cell(2 + i, 2, s)
IMPORTS = {'_meta': 'meta.csv', '_seg': 'segmentos.csv', '_ger': 'gerentes.csv', '_gs': 'gerentes_segmento.csv',
           '_rank': 'ranking.csv', '_cls': 'clases.csv', '_aum': 'aum_diario.csv', '_flj': 'flujo_diario.csv'}
TEST = os.environ.get('IF_TEST_DIR')   # prueba: datos estáticos (recortados) en lugar de IMPORTDATA


def estatico(ws, df):
    ws.append(list(df.columns))
    for row in df.itertuples(index=False):
        ws.append([None if (isinstance(v, float) and v != v) else (v.to_pydatetime() if hasattr(v, 'to_pydatetime') else v) for v in row])


def recorte(f):
    import pandas as pd
    d = pd.read_csv(os.path.join(TEST, f), parse_dates=[c for c in ('fecha', 'fecha_dato') if c in pd.read_csv(os.path.join(TEST, f), nrows=0).columns])
    if f == 'gerentes_segmento.csv':
        d = pd.concat([d[d.segmento == 'Total'].head(8), d[d.segmento == 'T+1'].head(8)])
    elif f == 'ranking.csv':
        d = d[d.subtipo == 'T+1'].head(10)
    elif f == 'clases.csv':
        d = d[d.clase.str.startswith(('IOL Cash Management - Clase A', 'Fima Ahorro Pesos - Clase A', 'Mercado Fondo'))]
    elif f in ('aum_diario.csv', 'flujo_diario.csv'):
        d = d.tail(30).round(1)
    elif f == 'segmentos.csv':
        d = d.round(4)
    elif f == 'gerentes.csv':
        d = d.head(10)
    elif f.startswith('serie/'):
        d = d.tail(30).round(6)
    return d


for tab, f in IMPORTS.items():
    ws = wb.create_sheet(tab)
    if TEST:
        estatico(ws, recorte(f))
    else:
        ws['A1'] = f'=IMPORTDATA(_cfg!$B$1&"{f}",",","en_US")'
for t, n in (('_rank', 4000), ('_cls', 4000), ('_gs', 3000)):
    grid_rows(wb[t], n)
ws = wb['_seg']; ws['AZ1'] = '=ARRAYFORMULA(IF(A1:A="",,A1:A&"|"&B1:B))'
# series por clase (A y B) y su recorte al período
for tab, sel in (('_sA', 'B10'), ('_sB', 'C10')):
    ws = wb.create_sheet(tab)
    grid_rows(ws, 4000)
    if TEST:
        estatico(ws, recorte('serie/' + ('3697' if tab == '_sA' else '734') + '.csv'))
    else:
        ws['A1'] = f'=IF(OR(Fondo!{sel}="",Fondo!{sel}="no encontrada"),"",IMPORTDATA(_cfg!$B$1&"serie/"&Fondo!{sel}&".csv",",","en_US"))'
D = wb.create_sheet('_fd')
grid_rows(D, 4000)
D['A1'] = 'A: fecha'; D['G1'] = 'B: fecha'; D['M1'] = 'Clase A'; D['N1'] = 'Clase B'; D['O1'] = 'AUM A (M)'; D['P1'] = 'Susc. A (M)'
# período: desde el último VCP <= Desde (base) hasta Hasta. Sin LET/LAMBDA "planos": el importador de xlsx de Google
# sólo acepta LAMBDA con la codificación de Excel (_xlfn.LAMBDA / _xlpm.x), verificado con archivos de prueba.
SCANMAX = '_xlfn.SCAN(0,{c}2:INDEX({c}2:{c},MAX(1,COUNT({c}2:{c}))),_xlfn.LAMBDA(_xlpm.a,_xlpm.x,MAX(_xlpm.a,_xlpm.x)))'
for c0, tab, wb_ in (('A', '_sA', 'W1'), ('G', '_sB', 'W2')):
    fe = f'{tab}!$A$2:$A'
    D[wb_] = f'=IFERROR(MAX(FILTER({fe},ISNUMBER({fe}),{fe}<=Fondo!$C$6)),MIN({fe}))'
    D[f'{c0}2'] = f'=IFERROR(FILTER({tab}!$A$2:$E,ISNUMBER({fe}),{fe}>=${wb_[0]}${wb_[1]},{fe}<=Fondo!$C$7),"")'
D['M2'] = '=ARRAYFORMULA(IF(ISNUMBER(B2:B),B2:B/B2*100,))'
NB = 'MAX(1,COUNT($G$2:$G))'
D['N2'] = (f'=IFERROR(_xlfn.MAP(FILTER(A2:A,ISNUMBER(A2:A)),_xlfn.LAMBDA(_xlpm.x,IFERROR(XLOOKUP(_xlpm.x,$G$2:INDEX($G$2:$G,{NB}),'
           f'$H$2:INDEX($H$2:$H,{NB}),,-1)/$H$2*100,))),)')
D['Q1'] = 'máx A'; D['R1'] = 'caída A'; D['S1'] = 'máx B'; D['T1'] = 'caída B'
D['Q2'] = '=IFERROR(' + SCANMAX.format(c='B') + ',)'
D['R2'] = '=ARRAYFORMULA(IF(ISNUMBER(Q2:Q),B2:B/Q2:Q-1,))'
D['S2'] = '=IFERROR(' + SCANMAX.format(c='H') + ',)'
D['T2'] = '=ARRAYFORMULA(IF(ISNUMBER(S2:S),H2:H/S2:S-1,))'
D['O2'] = '=ARRAYFORMULA(IF(ISNUMBER(C2:C),C2:C/1000000,))'
D['P2'] = '=ARRAYFORMULA(IF(ISNUMBER(D2:D),D2:D/1000000,))'
# series de industria para gráficos de Resumen
X = wb.create_sheet('_calc')
X['A1'] = 'fecha'; X['B1'] = 'AUM total (ARS billones)'; X['C1'] = 'fecha'; X['D1'] = 'Susc. netas totales (ARS M)'
X['A2'] = '=ARRAYFORMULA(IF(ISNUMBER(_aum!A2:A),_aum!A2:A,))'
X['B2'] = '=ARRAYFORMULA(IF(ISNUMBER(_aum!A2:A),INDEX(_aum!A2:Z,0,MATCH("Total",_aum!$1:$1,0))/1000000,))'
X['C2'] = '=ARRAYFORMULA(IF(ISNUMBER(_flj!A2:A),_flj!A2:A,))'
X['D2'] = '=ARRAYFORMULA(IF(ISNUMBER(_flj!A2:A),INDEX(_flj!A2:Z,0,MATCH("Total",_flj!$1:$1,0)),))'
colfmt(X, 'AC', 'dd/mm/yy')
colfmt(D, 'AG', FECHA)

# =============================================================== gráficos
NR = 520   # filas reservadas (≈ 2 años hábiles)
ch = LineChart(); ch.title = 'AUM de la industria (ARS billones)'; ch.style = 12
ch.add_data(Reference(X, min_col=2, min_row=1, max_row=NR), titles_from_data=True)
ch.set_categories(Reference(X, min_col=1, min_row=2, max_row=NR))
ch.legend = None; color_series(ch, [INDIGO]); ch.height = 8; ch.width = 16
ch.x_axis.number_format = 'mmm-yy'
R.add_chart(ch, 'A22')
ch = BarChart(); ch.type = 'col'; ch.title = 'Suscripciones netas diarias (ARS M)'; ch.style = 10
ch.add_data(Reference(X, min_col=4, min_row=1, max_row=NR), titles_from_data=True)
ch.set_categories(Reference(X, min_col=3, min_row=2, max_row=NR))
ch.legend = None; color_series(ch, [LILA], line=False); ch.height = 8; ch.width = 16; ch.gapWidth = 30
ch.x_axis.number_format = 'mmm-yy'
R.add_chart(ch, 'H22')
ch = BarChart(); ch.type = 'bar'; ch.title = 'AUM por tipo (ARS M)'; ch.style = 10
ch.add_data(Reference(R, min_col=4, min_row=12, max_row=18), titles_from_data=False)
ch.set_categories(Reference(R, min_col=1, min_row=12, max_row=18))
ch.legend = None; color_series(ch, [INDIGO], line=False); ch.height = 8; ch.width = 16
ch.x_axis.scaling.orientation = 'maxMin'
R.add_chart(ch, 'A39')
NF = 2000
ch = LineChart(); ch.title = 'Cuotaparte base 100 (moneda de cada clase)'; ch.style = 12
ch.add_data(Reference(D, min_col=13, max_col=14, min_row=1, max_row=NF), titles_from_data=True)
ch.set_categories(Reference(D, min_col=1, min_row=2, max_row=NF))
color_series(ch, [INDIGO, '00C08B']); ch.height = 8; ch.width = 18; ch.legend.position = 'b'
F.add_chart(ch, 'E4')
ch = LineChart(); ch.title = 'AUM clase A (millones, moneda de la clase)'; ch.style = 12
ch.add_data(Reference(D, min_col=15, min_row=1, max_row=NF), titles_from_data=True)
ch.set_categories(Reference(D, min_col=1, min_row=2, max_row=NF))
ch.legend = None; color_series(ch, [INDIGO]); ch.height = 7; ch.width = 18
F.add_chart(ch, 'E21')
ch = BarChart(); ch.type = 'col'; ch.title = 'Suscripciones netas diarias clase A (millones, moneda de la clase)'; ch.style = 10
ch.add_data(Reference(D, min_col=16, min_row=1, max_row=NF), titles_from_data=True)
ch.set_categories(Reference(D, min_col=1, min_row=2, max_row=NF))
ch.legend = None; color_series(ch, [LILA], line=False); ch.height = 7; ch.width = 18; ch.gapWidth = 30
F.add_chart(ch, 'E36')

for ws in wb.worksheets:
    if ws.title.startswith('_'):
        ws.sheet_state = 'hidden'
wb.active = 0
out = sys.argv[1] if len(sys.argv) > 1 else 'Industria_Fondos.xlsx'
if len(sys.argv) > 2:
    wb['_cfg']['B1'] = sys.argv[2]
wb.save(out)
print('ok', out)
