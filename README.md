# pySDIM Explorer

A map-based interface to download Catalan water data from
[SDIM](https://aplicacions.aca.gencat.cat/sdim21/), the data service of the
**Agència Catalana de l'Aigua (ACA)**, built on
[pySDIM](https://github.com/davitens/pySDIM).

Pick the water source, tick one or more data types and their parameters,
select stations on a map, and download everything as CSV files with
provenance. Data from before and since 2007 (two separate SDIM archives) come
in one go and are merged. The interface is in Catalan.

![pySDIM Explorer](screenshot/Captura%20de%20pantalla%202026-10-26%20130506.png)

> Not affiliated with or endorsed by the ACA or the Generalitat de Catalunya.

## Install

```bash
python -m pip install git+https://github.com/davitens/pySDIM_explorer.git
```

Python 3.10 or newer. This installs pySDIM from its GitHub `main` branch,
which has the dataset catalog the Explorer needs (newer than pySDIM 1.0.1 on
PyPI).

## Run

```bash
pysdim-explorer
```

A browser tab opens at http://127.0.0.1:8765. Options: `--port 8000` to use
another port, `--no-browser` to not open a tab. `python -m pysdim_explorer`
does the same. The server only listens on your own computer.

## How it works

1. **Font de dades** (data source): water source (rivers, reservoirs,
   groundwater, lakes and wetlands, coastal, protected zones) and, for
   protected zones, the zone. There is no period to choose: each data type
   covers both SDIM archives (before 2007 and since 2007) when it exists in
   both; types or parameters found in only one are tagged.
2. **Tipus de dades i paràmetres** (data types and parameters): tick one or
   more data types (physico-chemical, biological, priority substances,
   hydromorphological, flow, level, volume). Each ticked type opens its
   parameters grouped by family, with the number of station series that have
   each one.
3. **Estacions** (stations): the map shows the stations of every ticked type.
   Blue stations have at least one of the chosen parameters; orange ones are
   selected for download. Select them by clicking, or with the *Rectangle* or
   *Llaç* (lasso) tools, and switch to *Treu* to remove. Right-click a station
   to see its series. Stations can also be found by name or code.
4. **Descàrrega** (download): dates, output folder and file prefix. Each data
   type is downloaded with pySDIM (large requests are split automatically),
   from each archive over its part of the dates (up to 2006-12-31 from the
   pre-2007 archive, from 2007-01-01 from the current one), and the periods
   are merged. The first rows are previewed on the map.

## Output files

Per data type (`<key>` is e.g. `river_flow`, `groundwater_physico_chemical`):

| File | Content |
|---|---|
| `<prefix>_<key>_quality.csv` / `_quantity.csv` | Long table, one row per value, both periods. Adds `period` and `parameter` to pySDIM's columns. |
| `<prefix>_<key>_quality_wide.csv` / `_quantity_wide.csv` | One row per station and date, one column per parameter and unit (e.g. `Nitrats (mg NO3/L)`): a parameter measured in both periods is a single column. |
| `<prefix>_<key>_provenance.json` | pySDIM provenance of each SDIM report (source, URL, access time, query). |
| `<prefix>.zip` | All of the above. |

`parameter` is the SDIM series name, which is the same in both archives, so
field and lab measurements of one parameter (`pH (camp)`, `pH (lab)`) stay in
separate columns and are never averaged together. Station-specific series
names keep only what is measured (`E06_Vilanova Sau_Sau_Cabal sortida` →
`Cabal sortida`). Wide tables hold numeric values only; detection-limit
qualifiers (`<`) are in the long table.

Stations are matched by their SDIM code. Many keep their code across the two
archives (flows, levels, groundwater, coastal), so their series continue in
the same rows; pre-2007 river and reservoir quality stations have their own
codes and appear as separate points on the map. Before 2007, SDIM keeps river
and reservoir quality in one dataset, so both water sources show those
stations.

Station coordinates come from pySDIM's metadata (ETRS89 / UTM 31N) and are
converted to latitude/longitude for the map. Map tiles come from
OpenStreetMap, OpenTopoMap and Esri, so an internet connection is needed.

## Fair use and citation

pySDIM Explorer only talks to the public SDIM website through pySDIM; keep
request volumes moderate. The MIT license covers the code, not the data: when
you publish data obtained with it, cite the ACA as the source with the access
date (it is in each provenance JSON) and follow the Generalitat de Catalunya's
reuse conditions.

**Data:** Agència Catalana de l'Aigua (ACA), SDIM.
https://aplicacions.aca.gencat.cat/sdim21/

**Software:** see the [pySDIM citation](https://github.com/davitens/pySDIM#fair-use-and-citation)
(DOI: 10.5281/zenodo.22661965).

## License

[MIT](LICENSE)
