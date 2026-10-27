"""
Small daily extras (all free, no login):
  climate_state()   ENSO (NOAA CPC Oceanic Nino Index), IOD (NOAA PSL Dipole Mode Index, HadISST),
                    MJO (Australian Bureau of Meteorology RMM index)
  satellite_layers()  exact NASA GIBS names for geostationary infrared tiles (read from the GIBS listings)
  write_feed()      RSS feed of active storms (subscribe in any feed reader; no account needed)
  write_downloads() CSV of each storm's official track and hourly GFS diagnostics
"""
import csv, io, os, re
from email.utils import format_datetime
import numpy as np
import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; tc3d-site/1.0)"}
ONI = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
DMI = "https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data"
RMM = "http://www.bom.gov.au/climate/mjo/graphics/rmm.74toRealtime.txt"
GIBS_NRT = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/nrt"
IR_LAYERS = {"himawari": "Himawari_AHI_Band13_Clean_Infrared_v0_NRT_ZENJPEG", "goes_east": "GOES-East_ABI_Band13_Clean_Infrared_v0_NRT_ZENJPEG",
             "goes_west": "GOES-West_ABI_Band13_Clean_Infrared_v0_NRT_ZENJPEG"}


def _get(url, timeout=60):
    try:
        r = requests.get(url, headers=UA, timeout=timeout)
        return r.text if r.ok else None
    except requests.RequestException:
        return None


def climate_state():
    out = {}
    t = _get(ONI)                                         # SEAS YR TOTAL ANOM
    if t:
        rows = [l.split() for l in t.strip().splitlines()[1:] if len(l.split()) == 4]
        if rows:
            s, y, _, a = rows[-1]; a = float(a)
            phase = "El Niño" if a >= 0.5 else "La Niña" if a <= -0.5 else "neutral"
            out["enso"] = dict(index="ONI", value=a, season=f"{s} {y}", phase=phase, source="NOAA CPC")
    t = _get(DMI)                                         # year then 12 monthly values; -9999 missing
    if t:
        last = None
        for l in t.splitlines():
            p = l.split()
            if len(p) == 13 and p[0].isdigit():
                for m, v in enumerate(p[1:], 1):
                    try:
                        v = float(v)
                        if v > -99: last = (int(p[0]), m, v)
                    except ValueError:
                        pass
        if last:
            y, m, v = last
            out["iod"] = dict(index="DMI", value=round(v, 2), month=f"{y}-{m:02d}", source="NOAA PSL (HadISST)",
                              phase="positive" if v >= 0.4 else "negative" if v <= -0.4 else "neutral")
    t = _get(RMM)                                         # year month day RMM1 RMM2 phase amplitude ...
    if t:
        rows = [l.split() for l in t.splitlines() if re.match(r"\s*\d{4}\s", l)]
        rows = [r for r in rows if len(r) >= 7 and abs(float(r[3])) < 900]
        if rows:
            y, mo, d, r1, r2, ph, amp = rows[-1][:7]
            out["mjo"] = dict(index="RMM", phase=int(ph), amplitude=round(float(amp), 2), date=f"{y}-{int(mo):02d}-{int(d):02d}",
                              active=float(amp) >= 1, source="Australian Bureau of Meteorology")
    return out


def satellite_layers():
    """{key: {layer, tms}} for geostationary IR layers on GIBS (tile matrix set read from the listing)."""
    out = {}
    for key, layer in IR_LAYERS.items():
        t = _get(f"{GIBS_NRT}/{layer}/default/")
        if not t: continue
        m = re.findall(r'href="(GoogleMapsCompatible_Level\d+)/?"', t)
        if m: out[key] = dict(layer=layer, tms=m[0], max_level=int(m[0].split("Level")[1]))
    return out


def write_feed(storms, public, site_url):
    """RSS 2.0 feed of active storms, newest first."""
    now = pd.Timestamp.now("UTC")
    items = []
    for s in sorted([x for x in storms if x.get("status") == "active"], key=lambda x: -x.get("vmax_kt", 0)):
        title = f"{s['name']}: {s['label']}, {s['vmax_kt']} kt ({s['basin_name']})" + (" · rapid intensification" if s.get("ri") else "")
        link = f"{site_url.rstrip('/')}/#storm={s['key']}"
        desc = (s.get("story") or [""])[0]
        items.append(f"<item><title>{_x(title)}</title><link>{_x(link)}</link><guid isPermaLink=\"false\">{s['key']}-{now:%Y%m%d}</guid>"
                     f"<pubDate>{format_datetime(now.to_pydatetime())}</pubDate><description>{_x(desc)}</description></item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f"<title>Tropical cyclones: daily update</title><link>{_x(site_url)}</link>"
           "<description>Every active tropical cyclone, updated daily from GFS analyses and official advisories.</description>"
           f"<lastBuildDate>{format_datetime(now.to_pydatetime())}</lastBuildDate>{''.join(items)}</channel></rss>")
    open(os.path.join(public, "feed.xml"), "w").write(xml)


def _x(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_downloads(storms, public):
    """data/downloads/<key>_track.csv (official track) and <key>_hourly.csv (GFS diagnostics) for every storm."""
    d = os.path.join(public, "data", "downloads"); os.makedirs(d, exist_ok=True)
    for s in storms:
        with open(os.path.join(d, f"{s['key']}_track.csv"), "w", newline="") as f:
            w = csv.writer(f); w.writerow(["time_utc", "lat", "lon", "vmax_kt_1min"])
            for row in s.get("track", []): w.writerow(row)
        diag = s.get("diag") and os.path.join(public, s["diag"])
        if diag and os.path.exists(diag):
            import json
            j = json.load(open(diag)); keys = [k for k, v in j.items() if isinstance(v, list) and len(v) == len(j.get("time", []))]
            with open(os.path.join(d, f"{s['key']}_hourly.csv"), "w", newline="") as f:
                w = csv.writer(f); w.writerow(keys)
                for i in range(len(j["time"])): w.writerow([j[k][i] for k in keys])
            s["download_hourly"] = f"data/downloads/{s['key']}_hourly.csv"
        s["download_track"] = f"data/downloads/{s['key']}_track.csv"
