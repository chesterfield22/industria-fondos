# Industria Fondos — base y métricas diarias de FCI (Argentina)

Pipeline diario (GitHub Actions) que reconstruye la industria de fondos comunes de inversión a partir de la **planilla diaria de cuotapartes de la CNV** y publica CSV livianos para un Google Sheets ("Industria Fondos") y una base Parquet consultable con DuckDB.

## Datos publicados (rama `data`)

Base: `https://raw.githubusercontent.com/chesterfield22/industria-fondos/data/`

| Archivo | Contenido |
|---|---|
| `meta.csv` | fecha de corte, cobertura, tipos de cambio, fuentes |
| `segmentos.csv` | AUM, suscripciones netas (1d, 7d, MTD, 30d, 90d, YTD, 365d) y rendimientos por tipo/subtipo (esquema 1816) |
| `gerentes.csv`, `gerentes_tipo.csv`, `gerentes_subtipo.csv`, `tipo_gerente.csv` | sociedades gerentes, bancarias vs independientes |
| `gerentes_segmento.csv` | gerente × segmento (Total / tipo / subtipo): posición, AUM, share, suscripciones |
| `gerentes_iol.csv` | idem con IOL como gerente separado de Adcap (`IOL`, `Adcap ex IOL`) |
| `gerentes_iol_sin_mm.csv` | idem `gerentes_iol.csv`, excluyendo gerentes con al menos un fondo Money Market (posición y share dentro de ese universo) |
| `iol_adcap_diario.csv`, `iol_adcap_mensual.csv` | AUM de IOL y de Adcap (clases ARS, clases USD, total), participación de IOL y posición en el ranking de gerentes |
| `ranking.csv` | clases de fondos con AUM ≥ ARS 1.000 M: rendimientos, posición en su subtipo, volatilidad 90d, rend/vol, honorarios |
| `clases.csv` | catálogo de clases activas (selector) |
| `aum_diario.csv`, `flujo_diario.csv` | series diarias por tipo (ARS M) |
| `fx.csv` | A3500, MEP, CCL |
| `serie/<clase_id>.csv` | historia completa por clase: fecha, vcp, aum, suscripciones_netas, rend_diario (moneda de la clase) |
| `base/*.parquet` | base diaria completa por clase (2020–hoy) y tabla maestra |

## Metodología

- **Suscripciones netas** (por clase y día): `AUM_t − AUM_{t−1} × VCP_t / VCP_{t−1}`. Se suman clases y días; los flujos de clases USD se convierten a pesos al tipo de cambio de cada día.
- **Rendimientos** por horizonte: `VCP_T / VCP_base − 1`, con base = último VCP ≤ T − h días corridos (MTD: último del mes anterior; YTD: último del año anterior). Segmentos: promedio ponderado por AUM, neto de comisiones. Verificado contra el informe 1816 del 02/11/2026 (28 de 28 rendimientos de clase idénticos).
- **Fecha de corte**: última fecha hábil con ≥ 97% de las clases reportadas y ≥ 98,5% del AUM esperado (la CNV recibe reportes tardíos; la corrida de las 10:00 completa el día anterior). Clases que no reportaron T arrastran su último dato (máx. 7 días) y se informa el % de AUM arrastrado.
- **Tipo de cambio**: A3500 (BCRA, Com. A 3500) por defecto; también MEP y CCL. El informe 1816 usa un tipo de cambio propio (≈ 1.613 al 02/11/2026, no publicado).
- **Segmentación**: subtipos 1816 tomados del propio informe (≈ 97% del AUM), regla por categoría CAFCI para categorías unívocas, y para el resto el subtipo cuyo rendimiento diario (120 ruedas) más se parece. Correcciones manuales en `ref/segmento_manual.csv` (fondo_id, subtipo).
- **Tipo de gerente** (bancaria / independiente): tomado del informe 1816; manuales en `ref/gerente_tipo_manual.csv`.
- **Calidad de datos**: se eliminan picos transitorios de VCP (>40%) y AUM (×5) que revierten en ≤ 5 ruedas; saltos permanentes de VCP > 40% son "quiebres" (no se mide rendimiento ni flujo a través de ellos); tramos de AUM informados en otra moneda se normalizan; días con salto > 10% (ARS) / 5% (USD) no computan flujo.

## Validar contra un informe 1816

```
python pipeline/validar_1816.py Informe_Diario_FCI_AAAAMMDD.pdf base validacion.csv
```

## Consultas con DuckDB

```sql
INSTALL httpfs; LOAD httpfs;
SELECT * FROM read_parquet('https://raw.githubusercontent.com/chesterfield22/industria-fondos/data/base/clases_diario_2026.parquet')
WHERE clase_id = '3697' ORDER BY fecha DESC LIMIT 10;
```

Fuentes: CNV (planilla diaria de cuotapartes) vía [ArgentinaDatos](https://github.com/enzonotario/esjs-argentina-datos-api); BCRA (A3500); ArgentinaDatos/dolarapi (MEP, CCL); 1816 (segmentación y tipo de gerente).

## Google Sheets "Industria Fondos"

`sheets/build_sheet.py` genera la plantilla (sin datos): pestañas visibles Resumen, Segmentos, Gerentes, Ranking, Fondo y Notas; pestañas ocultas `_xxx` con `IMPORTDATA(<URL base> & archivo, ",", "en_US")`. La primera vez Google pide "Permitir acceso" a datos externos.

```
python sheets/build_sheet.py "Industria Fondos.xlsx"
```

Nota técnica: el importador de xlsx de Google sólo acepta LAMBDA/MAP/SCAN con la codificación de Excel (`_xlfn.LAMBDA`, `_xlpm.x`); la plantilla evita `LET` y usa `MATCH` sobre encabezados para ubicar columnas por nombre.
