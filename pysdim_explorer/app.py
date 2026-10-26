"""pySDIM Explorer: a local web app to pick SDIM datasets, parameters and
stations on a map, then download the data with pySDIM.

Run ``pysdim-explorer`` (or ``python -m pysdim_explorer``) and a browser tab
opens at http://127.0.0.1:8765. Only the standard library is needed on top of
pySDIM; the map (Leaflet) is loaded from a CDN by the page.
"""

from __future__ import annotations

import argparse
import functools
import json
import math
import os
import sys
import threading
import traceback
import uuid
import warnings
import webbrowser
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

from sdim import SDIM, Catalog, Query, SDIMError, SDIMNoData
from sdim.catalog import _norm
from sdim.resolver import (
    DATA_SOURCES, PROTECTED_ZONES, _group_mask, _source_target)

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_OUT = Path.cwd() / "sdim_downloads"
SHEETS = {"quality": "qualitat", "quantity": "quantitat"}
# SDIM keeps data before 2007 in a separate archive: each period is its own
# target, queried over its part of the requested dates and merged afterwards
PERIODS = ("before_2007", "after_2007")
PERIOD_SPAN = {"before_2007": (None, "2006-12-31"), "after_2007": ("2007-01-01", None)}
PERIOD_LABELS = {"before_2007": "abans del 2007", "after_2007": "des del 2007"}
OTHER = "_other"  # prefix of pseudo-parameters for series matching no catalog parameter

# labels shown in the (Catalan) interface
LABELS = {
    "river": "Rius", "reservoir": "Embassaments", "groundwater": "Aigües subterrànies",
    "lakes_and_wetlands": "Estanys i zones humides", "coastal": "Aigües costaneres",
    "protected": "Zones protegides",
    "physico_chemical": "Fisicoquímics", "biological": "Biològics",
    "priority_substances": "Substàncies prioritàries", "hydromorphological": "Hidromorfològics",
    "flow": "Cabal", "level": "Nivell", "volume": "Volum",
    "drinking_water": "Abastament d'aigua potable", "river_reserves": "Reserves naturals fluvials",
    "nitrate_vulnerable": "Zones vulnerables als nitrats",
    "eutrophication_sensitive": "Zones sensibles a l'eutrofització",
}

catalog = Catalog()
jobs: dict[str, dict] = {}


# -- coordinates -------------------------------------------------------------

def utm_to_latlon(x: float, y: float, zone: int = 31) -> tuple[float, float]:
    """ETRS89 / UTM (EPSG:25831 for Catalonia) to WGS84 lat/lon degrees."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x -= 500000.0
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    t1 = math.tan(phi1) ** 2
    c1 = ep2 * math.cos(phi1) ** 2
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
           ) / math.cos(phi1)
    return math.degrees(lat), zone * 6 - 183 + math.degrees(lon)


def _latlon(x: str, y: str):
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None
    # anything outside Catalonia's UTM 31N box is a placeholder (0) or a typo
    if not (250000 <= fx <= 560000 and 4480000 <= fy <= 4770000):
        return None
    lat, lon = utm_to_latlon(fx, fy)
    return round(lat, 6), round(lon, 6)


# -- catalog queries -----------------------------------------------------------

def sources() -> dict:
    out = []
    for source, types in DATA_SOURCES.items():
        out.append({
            "id": source, "label": LABELS.get(source, source),
            "data_types": [{"id": t, "label": LABELS.get(t, t),
                            "periods": [p for p in PERIODS if p in per]}
                           for t, per in types.items()],
        })
    zones = [{"id": z, "label": LABELS.get(z, z), "subnetwork": s}
             for z, s in PROTECTED_ZONES.items()]
    return {"sources": out, "protected_zones": zones}


def _match_series(name: str, variables: list[tuple[str, str]]) -> str:
    """Catalog parameter ``v`` a station series belongs to: the exact name, or
    else the longest parameter name the series name contains (flow and level
    series are named ``EA051_<town>_Cabal riu <river>``)."""
    n = _norm(str(name)).strip()
    best, best_len = OTHER, 0
    for v, vname in variables:
        if n == vname:
            return v
        if vname and vname in n and len(vname) > best_len:
            best, best_len = v, len(vname)
    if best == OTHER:
        # group the rest by their kind: "E06_Sau_Volum embassament" -> "Volum"
        tail = str(name).rsplit("_", 1)[-1].split()
        best = f"{OTHER}:{tail[0]}" if tail else OTHER
    return best


@functools.lru_cache(maxsize=32)
def target_info(source: str, dtype: str, period: str, zone: str | None) -> dict:
    target_id, groups = _source_target(catalog, source, dtype, period, zone or None, None)
    _, net, sub = target_id.split(":")
    tv = catalog.variables_for(net, sub, period=period)
    if groups:
        tv = tv[_group_mask(tv, groups)]
    tv = tv.drop_duplicates("v")
    variables = [{"g": r.g, "g_name": r.g_name, "f": r.f, "f_name": r.f_name,
                  "v": r.v, "v_name": r.v_name} for r in tv.itertuples()]
    keys = [(r.v, _norm(str(r.v_name)).strip()) for r in tv.itertuples()]
    allowed = set(tv.v)

    sv = catalog.target_station_variables(target_id)
    match = {n: _match_series(n, keys) for n in sv.variable_name.unique()}
    stations: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in sv.itertuples():
        v = match[r.variable_name]
        other = v.startswith(OTHER)
        if not other and v not in allowed:
            continue
        if groups and other:
            continue  # series outside this data type's groups
        st = stations.get(r.station)
        if st is None:
            ll = _latlon(r.x, r.y)
            st = stations[r.station] = {
                "code": r.station, "name": r.station_name,
                "lat": ll[0] if ll else None, "lon": ll[1] if ll else None,
                "x": r.x, "y": r.y, "series": []}
        st["series"].append([r.variable_id, r.variable_name, v])
        counts[v] = counts.get(v, 0) + 1
    for v in sorted(k for k in counts if k.startswith(OTHER)):
        kind = v.partition(":")[2] or "Altres"
        variables.append({"g": "", "g_name": "Altres sèries", "f": "", "f_name": "Altres sèries",
                          "v": v, "v_name": f"{kind} … (sèries amb nom per estació)"})
    row = catalog.target(target_id)
    return {
        "target_id": target_id, "groups": groups or [],
        "network_name": row["network_name"], "subnetwork_name": row["subnetwork_name"],
        "variables": variables, "series_counts": counts,
        "stations": list(stations.values()),
    }


def _zone_of(target_id: str) -> str | None:
    sub = target_id.split(":")[2]
    return next((z for z, s in PROTECTED_ZONES.items() if s == sub), None)


@functools.lru_cache(maxsize=32)
def type_info(source: str, dtype: str, zone: str | None) -> dict:
    """One data type over both periods: its targets, its parameters merged by
    SDIM parameter code (shared by both periods) and its stations merged by
    station code. Station series are ``[period, series id, name, parameter]``."""
    parts: dict[str, dict] = {}
    for period in ("after_2007", "before_2007"):
        if period not in DATA_SOURCES[source][dtype]:
            continue
        z = zone
        if source == "protected" and not z and "after_2007" in parts:
            z = _zone_of(parts["after_2007"]["target_id"])  # same zone in both periods
        try:
            parts[period] = target_info(source, dtype, period, z)
        except SDIMError:
            continue  # e.g. a protected zone without pre-2007 data
    if not parts:
        raise SDIMError("SDIM no té dades d'aquest tipus.")

    variables: dict[str, dict] = {}
    counts: dict[str, int] = {}
    stations: dict[str, dict] = {}
    for period, info in parts.items():
        for v in info["variables"]:
            variables.setdefault(v["v"], {**v, "periods": []})["periods"].append(period)
        for v, n in info["series_counts"].items():
            counts[v] = counts.get(v, 0) + n
        for st in info["stations"]:
            m = stations.get(st["code"])
            if m is None:
                m = stations[st["code"]] = {**st, "series": [], "periods": []}
            elif m["lat"] is None and st["lat"] is not None:
                m.update(lat=st["lat"], lon=st["lon"], x=st["x"], y=st["y"])
            m["periods"].append(period)
            m["series"] += [[period, *s] for s in st["series"]]
    others = sorted((v for v in variables.values() if v["v"].startswith(OTHER)),
                    key=lambda v: v["v"])
    return {
        "source": source, "data_type": dtype, "label": LABELS.get(dtype, dtype),
        "targets": [{"period": p, "target_id": i["target_id"], "groups": i["groups"],
                     "network_name": i["network_name"], "subnetwork_name": i["subnetwork_name"],
                     "stations": len(i["stations"])} for p, i in parts.items()],
        "variables": [v for v in variables.values() if not v["v"].startswith(OTHER)] + others,
        "series_counts": counts,
        "stations": list(stations.values()),
    }


# -- downloads -----------------------------------------------------------------

class LoggingSDIM(SDIM):
    """SDIM client that reports each report it generates to a job log."""

    def __init__(self, log, **kw):
        super().__init__(**kw)
        self._log = log
        self._n = 0

    def _report(self, query):
        self._n += 1
        self._log(f"Informe {self._n}: {len(query.stations)} estació(ns), "
                  f"{query.start} → {query.end}")
        status, data = super()._report(query)
        note = {200: "correcte", 201: "sense registres", 202: "massa gran, es divideix"}.get(status, status)
        self._log(f"  → {note}")
        return status, data


def build_query(part: dict, start: str, end: str) -> Query:
    """Query for one data type in one period: its stations, their series and
    the kinds of the chosen parameters (all kinds when per-station series are
    included)."""
    period, net, sub = part["target_id"].split(":")
    stations = {code: ids for code, ids in part["stations"].items() if ids}
    tv = catalog.variables_for(net, sub, period=period)
    params = part.get("parameters", [])
    chosen = [v for v in params if not v.startswith(OTHER)]
    if chosen and len(chosen) == len(params):
        tv = tv[tv.v.isin(chosen)]
    kinds = ([("g", x) for x in tv.g.unique()] + [("f", x) for x in tv.f.unique()]
             + [("v", x) for x in tv.v.unique()])
    return Query(period=period, networks=[net], subnetworks=[sub], variable_kinds=kinds,
                 ambit="catalunya", stations=stations, start=start, end=end)


def clip_dates(period: str, start: str, end: str) -> tuple[str, str] | None:
    """The part of ``start``..``end`` (ISO dates) that falls in ``period``."""
    lo, hi = PERIOD_SPAN[period]
    s, e = max(start, lo) if lo else start, min(end, hi) if hi else end
    return (s, e) if s <= e else None


def parameter_name(variable) -> str:
    """Column name of an SDIM series. Series names are the same in both
    periods, so they merge the periods without mixing field and lab series
    of one parameter. Station-prefixed names keep only what is measured:
    ``E06_Vilanova Sau_Sau_Cabal sortida`` -> ``Cabal sortida``,
    ``08001-0039_Abrera_AB2_Nivell piezometric_Manual`` -> ``Nivell piezometric Manual``."""
    if pd.isna(variable):
        return variable
    parts = str(variable).split("_")
    if len(parts) == 1:
        return parts[0].strip()
    tail = parts[-1].strip()
    if " " not in tail and len(parts) > 2:
        tail = f"{parts[-2].strip()} {tail}"
    return tail


def add_parameter(df: pd.DataFrame, period: str) -> pd.DataFrame:
    """Add ``period`` and ``parameter`` (see :func:`parameter_name`)."""
    df = df.copy()
    df.insert(df.columns.get_loc("variable"), "parameter", df["variable"].map(parameter_name))
    df.insert(0, "period", period)
    return df


def wide_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per station and date, one column per parameter (and unit):
    values from both periods land in the same column."""
    d = df.dropna(subset=["value"]).copy()
    if d.empty:
        return pd.DataFrame()
    d["column"] = d["parameter"].astype(str)
    if "unit" in d:
        # units written differently ("mg/l", "mg/L") share a column
        unit = d["unit"].fillna("").astype(str).str.strip()
        first: dict[str, str] = {}
        for k, u in zip(unit.str.lower(), unit):
            first.setdefault(k, u)
        shown = unit.str.lower().map(first)
        d["column"] = d["column"].where(shown == "", d["column"] + " (" + shown + ")")
    d["station_name"] = d["station_name"].fillna("") if "station_name" in d else ""
    w = d.pivot_table(index=["station_code", "station_name", "date"], columns="column",
                      values="value", aggfunc="mean")
    w.columns.name = None
    return w.reset_index()


def run_job(job: dict, req: dict) -> None:
    """Download every selected data type in each period it has data for,
    merge the periods and save one long and one wide CSV per data type and
    sheet, a provenance JSON per data type and a ZIP with everything."""
    def log(msg):
        job["log"].append(msg)

    types = [t for t in req.get("types", [])
             if any(any(p["stations"].values()) for p in t["parts"])]
    out_dir = Path(req.get("out_dir") or DEFAULT_OUT).expanduser()
    prefix = req.get("prefix") or f"sdim_{date.today():%Y%m%d}"
    job.update(files=[], summary=[], preview={}, out_dir=str(out_dir))
    failed = 0
    try:
        if not types:
            raise SDIMError("No s'ha seleccionat cap sèrie d'estació.")
        out_dir.mkdir(parents=True, exist_ok=True)
        aca = LoggingSDIM(log, delay=float(req.get("delay", 0.6)))
        try:
            for t in types:
                info = type_info(t["source"], t["data_type"], t.get("zone") or None)
                label = info["label"]
                sheets: dict[str, list[pd.DataFrame]] = {}
                prov = []
                for part in t["parts"]:
                    period = part["target_id"].split(":")[0]
                    span = clip_dates(period, req["start"], req["end"])
                    if span is None or not any(part["stations"].values()):
                        continue
                    query = build_query(part, *span)
                    n_series = sum(len(v) for v in query.stations.values())
                    log(f"— {label}, {PERIOD_LABELS[period]} ({part['target_id']}): "
                        f"{len(query.stations)} estació(ns), {n_series} sèries, {span[0]} → {span[1]}")
                    try:
                        with warnings.catch_warnings(record=True) as caught:
                            warnings.simplefilter("always")
                            result = aca.get_data(query, max_requests=int(req.get("max_requests", 64)))
                        for w in caught:
                            log(f"Avís: {w.message}")
                    except SDIMNoData:
                        log("Sense dades en aquest període.")
                        continue
                    except Exception as exc:  # keep going with the other parts
                        failed += 1
                        log(f"Error: {exc}")
                        job["trace"] = traceback.format_exc()
                        continue
                    prov.append(result.provenance)
                    for sheet, df in result.items():
                        if len(df):
                            sheets.setdefault(sheet, []).append(add_parameter(df, period))
                if not sheets:
                    continue
                key = f"{t['source']}_{t['data_type']}"
                for sheet, frames in sheets.items():
                    df = pd.concat(frames, ignore_index=True)
                    df = df.sort_values([c for c in ("station_code", "parameter", "date")
                                         if c in df.columns], kind="stable")
                    long_path = out_dir / f"{prefix}_{key}_{sheet}.csv"
                    df.to_csv(long_path, index=False)
                    wide = wide_table(df)
                    wide_path = out_dir / f"{prefix}_{key}_{sheet}_wide.csv"
                    wide.to_csv(wide_path, index=False)
                    job["files"] += [str(long_path), str(wide_path)]
                    job["summary"].append({
                        "label": label, "sheet": sheet, "rows": len(df),
                        "stations": int(df["station_code"].nunique()),
                        "parameters": int(df["parameter"].nunique()),
                        "periods": [PERIOD_LABELS[p] for p in PERIODS if p in set(df["period"])],
                        "first": str(df["date"].min())[:10], "last": str(df["date"].max())[:10],
                    })
                    cols = [c for c in ("period", "date", "station_code", "station_name", "parameter",
                                        "value", "qualifier", "unit") if c in df.columns]
                    head = df[cols].head(100)
                    job["preview"][f"{label} · {SHEETS.get(sheet, sheet)}"] = {
                        "columns": cols,
                        "rows": head.astype(object).where(head.notna(), "").astype(str).values.tolist(),
                    }
                    n_cols = len(wide.columns) - 3 if len(wide) else 0
                    log(f"{label} · {SHEETS.get(sheet, sheet)}: {len(df)} files, "
                        f"{n_cols} columnes de paràmetres")
                prov_path = out_dir / f"{prefix}_{key}_provenance.json"
                prov_path.write_text(json.dumps(prov, indent=2, ensure_ascii=False), encoding="utf-8")
                job["files"].append(str(prov_path))
        finally:
            aca.close()

        if job["files"]:
            zip_path = out_dir / f"{prefix}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in job["files"]:
                    zf.write(p, Path(p).name)
            job["zip"] = str(zip_path)
            log(f"Desat a {out_dir}")
            job["state"] = "partial" if failed else "done"
        else:
            job["state"] = "error" if failed else "nodata"
    except Exception as exc:  # report every failure to the page
        log(f"Error: {exc}")
        job["trace"] = traceback.format_exc()
        job["state"] = "error"


# -- HTTP ----------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def _send(self, body: bytes, ctype: str, status: int = 200, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status: int = 200):
        self._send(json.dumps(obj, ensure_ascii=False).encode(), "application/json", status)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if url.path in ("/", "/index.html"):
                self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/sources":
                self._json({**sources(), "default_out": str(DEFAULT_OUT)})
            elif url.path == "/api/type":
                self._json(type_info(q["source"], q["data_type"], q.get("zone") or None))
            elif url.path == "/api/job":
                job = jobs.get(q.get("id", ""))
                if job is None:
                    return self._json({"error": "feina desconeguda"}, 404)
                self._json(job)
            elif url.path == "/api/zip":
                job = jobs.get(q.get("id", ""))
                if not job or not job.get("zip"):
                    return self._json({"error": "no hi ha cap fitxer"}, 404)
                p = Path(job["zip"])
                self._send(p.read_bytes(), "application/zip", extra={
                    "Content-Disposition": f'attachment; filename="{p.name}"'})
            else:
                self._json({"error": "no trobat"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):
        url = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
            if url.path == "/api/download":
                job_id = uuid.uuid4().hex[:12]
                job = jobs[job_id] = {"id": job_id, "state": "running", "log": []}
                threading.Thread(target=run_job, args=(job, req), daemon=True).start()
                self._json({"id": job_id})
            elif url.path == "/api/open":
                folder = Path(req.get("path") or DEFAULT_OUT)
                folder.mkdir(parents=True, exist_ok=True)
                if sys.platform == "win32":
                    os.startfile(folder)  # noqa: S606
                self._json({"ok": True})
            else:
                self._json({"error": "no trobat"}, 404)
        except Exception as exc:
            self._json({"error": str(exc)}, 400)


def main() -> None:
    ap = argparse.ArgumentParser(description="pySDIM Explorer: download SDIM data from a map")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"pySDIM Explorer a {url}  (Ctrl+C per aturar)")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
