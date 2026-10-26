# pySDIM Explorer

A map-based interface to download Catalan water data from
[SDIM](https://aplicacions.aca.gencat.cat/sdim21/), the data service of the
**Agència Catalana de l'Aigua (ACA)**, built on
[pySDIM](https://github.com/davitens/pySDIM).

Pick the water source, tick one or more data types and their parameters,
select stations on a map, and download everything as CSV files with
provenance. The interface is in Catalan.

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

1. **Font de dades** (data source): period (from 2007 or before 2007), water
   source (rivers, reservoirs, groundwater, lakes and wetlands, coastal,
   protected zones) and, for protected zones, the zone.
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
   type is downloaded with pySDIM (large requests are split automatically) and
   saved as `<prefix>_<dataset>_quality.csv` / `_quantity.csv` plus a
   provenance JSON, all also packed in one ZIP. The first rows are previewed
   on the map.

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
