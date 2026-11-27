# Carteras de los FCI (rama `carteras`)

Fuente: ficha de cada fondo en la CNV, sección "Composición Semanal Cartera"
(`https://www.cnv.gov.ar/SitioWeb/FondosComunesInversion/DetallesFCI/{id}`). Es la cartera que la sociedad gerente
remite semanalmente por el Sistema CNV-CAFCI. Porcentajes **sobre el patrimonio neto** del fondo, tal como los informa
la CNV (por eso un fondo que toma caución o tiene pasivos puede sumar más de 100% en activos).

## Archivos

| Archivo | Capa | Contenido |
|---|---|---|
| `historico/AAAA-MM-DD.parquet` | crudo | Una fila por renglón de la cartera (rubro, especie o total) tal como la CNV lo muestra, por fecha de cartera. Columnas: `fecha_cartera, ficha_id, fondo, gerente, depositaria, clasificacion, moneda_fondo, region, horizonte, pn, moneda_pn, orden, nivel, rubro, especie, pct, relevado`. |
| `derivado/ultimas.parquet` | transformado | Última cartera de cada fondo + `tipo`, `emisor`, `moneda` (estimados por reglas sobre el nombre de la especie), `fondo_id`/`subtipo` de la planilla diaria, `pct_ant` (cartera anterior), `n_fci` (cuántos fondos tienen la especie). |
| `indice.csv` | Sheet | Un fondo por fila (selector de la hoja Carteras). |
| `f/<ficha_id>.csv` | Sheet | Todo lo que muestra la hoja para un fondo. Columna `s`: `m` ficha y métricas, `t` por tipo de activo, `c` por moneda, `r` rubros como la CNV, `e` especies, `b` bajas vs cartera anterior, `p` fondos parecidos. |
| `estado.json`, `meta.csv` | control | Fecha centinela, fondos pendientes de reintento, conteos. |

## Consultas con DuckDB

```python
import duckdb   # sobre un clon de la rama: git clone -b carteras --depth 1 https://github.com/chesterfield22/industria-fondos carteras
duckdb.sql("""
  select fecha_cartera, fondo, especie, pct
  from 'carteras/historico/*.parquet'
  where nivel = 'especie' and especie ilike '%T31Y7%'
  order by fecha_cartera, pct desc
""").df()
```

## Métricas (hoja Carteras)

- **Activos / PN**: suma de los rubros de activo informados. **Activos − 100%**: financiamiento o pasivos netos implícitos (p. ej. caución tomadora).
- **Liquidez inmediata**: Disponibilidades + cauciones y pases colocadores.
- **Top 1/5/10** y **HHI** (0–10.000) por especie y por emisor. Pagarés, cheques y FCE no informan librador: quedan fuera del cálculo por emisor (ver "% sin emisor identificado").
- **Fondos parecidos**: similitud coseno entre las carteras (pesos normalizados) y **superposición** = suma de los mínimos de los pesos (qué % de la cartera es común).
- **Avisos**: cartera desactualizada (más de 14 días detrás de la más reciente del sistema), datos atípicos (activos < 50% o > 200% del PN, o pasivos > 50%), sin especies, no vinculado a la planilla, patrimonio CNV distinto del AUM de la planilla (> 5%).
