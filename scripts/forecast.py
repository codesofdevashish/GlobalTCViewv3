"""
Forecast tracks from NCEP's operational cyclone tracker (free, no login):
  GEFS ensemble members (AP01-AP30), control (AC00), ensemble mean (AEMN)  -> ens_tracker/prod on NOMADS
  GFS deterministic (AVNO)                                                  -> gfs/prod/.../atmos on NOMADS
Files are found by reading the NOMADS directory listings (any file whose name contains "atcfunix"),
so small changes in file naming do not break this. ATCF lines:
  BASIN, CY, YYYYMMDDHH, TECHNUM, TECH, TAU, LatN/S, LonE/W, VMAX(kt), MSLP, ...
These are model guidance, not official forecasts.
"""
import re
import numpy as np
import pandas as pd
import requests

NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com"
ATCF_BASIN = {"L": "AL", "E": "EP", "C": "CP", "W": "WP", "A": "IO", "B": "IO", "S": "SH", "P": "SH", "U": "SH", "Q": "SL"}


def _links(url, sess):
    try:
        r = sess.get(url, timeout=60)
        if r.status_code != 200: return []
        return [h for h in re.findall(r'href="([^"?#]+)"', r.text) if not h.startswith(("/", "http", "..")) and h != "./"]
    except requests.RequestException:
        return []


def _atcf_files(url, sess, depth=2):
    out = []
    for h in _links(url, sess):
        if h.endswith("/") and depth > 0: out += _atcf_files(url + h, sess, depth - 1)
        elif "atcfunix" in h and "gen" not in h: out.append(url + h)
    return out


def _ll(tok):
    v = int(tok[:-1]) / 10.0
    return -v if tok[-1] in "SW" else v


def parse_atcf(text):
    rows = []
    for line in text.splitlines():
        t = [x.strip() for x in line.split(",")]
        if len(t) < 9 or not t[6] or not t[7]: continue
        try:
            rows.append(dict(basin=t[0], cy=int(t[1]), cycle=pd.Timestamp(t[2][:8] + " " + t[2][8:10] + ":00"),
                             tech=t[4].upper(), tau=int(t[5]), lat=_ll(t[6]), lon=_ll(t[7]), kt=int(t[8] or 0),
                             mslp=int(t[9]) if len(t) > 9 and t[9].isdigit() else None))
        except (ValueError, IndexError):
            continue
    return pd.DataFrame(rows)


def fetch_guidance(latest, sess=None):
    """All tracker lines for the newest cycle at or before `latest` that has files (tries the last 4 cycles)."""
    sess = sess or requests.Session(); sess.headers.setdefault("User-Agent", "tc3d-site/1.0")
    for k in range(4):
        cyc = pd.Timestamp(latest) - pd.Timedelta(hours=6 * k)
        d, h = f"{cyc:%Y%m%d}", f"{cyc:%H}"
        files = _atcf_files(f"{NOMADS}/ens_tracker/prod/gefs.{d}/{h}/", sess)
        files += [f for f in _atcf_files(f"{NOMADS}/gfs/prod/gfs.{d}/{h}/atmos/", sess, depth=0) if "track" in f]
        frames = []
        for f in files:
            try:
                r = sess.get(f, timeout=60)
                if r.ok and r.text.strip(): frames.append(parse_atcf(r.text))
            except requests.RequestException:
                pass
        df = pd.concat([x for x in frames if len(x)], ignore_index=True) if frames else pd.DataFrame()
        if len(df):
            df = df[df.cycle == df.cycle.max()].drop_duplicates(["basin", "cy", "tech", "tau"], keep="last")
            print(f"forecast guidance: cycle {df.cycle.max()}, {df.tech.nunique()} tracks from {len(files)} files")
            return df
    print("forecast guidance: none found")
    return pd.DataFrame()


def _km(a, b):
    p = np.deg2rad
    return 6371 * 2 * np.arcsin(np.sqrt(np.sin(p(b[0] - a[0]) / 2) ** 2 +
                                        np.cos(p(a[0])) * np.cos(p(b[0])) * np.sin(p((b[1] - a[1] + 540) % 360 - 180) / 2) ** 2))


def for_storm(df, sid):
    """Guidance for one TCVitals storm id (e.g. 05B) -> dict for the website, or None."""
    if df is None or not len(df): return None
    g = df[(df.basin == ATCF_BASIN.get(sid[-1], "")) & (df.cy == int(sid[:2]))]
    if not len(g): return None
    tracks = {}
    for tech, t in g.groupby("tech"):
        t = t.sort_values("tau")
        lon = np.rad2deg(np.unwrap(np.deg2rad(t.lon.values)))            # continuous across the dateline
        tracks[tech] = [[int(a), round(float(b), 2), round(float(c), 2), int(k)] for a, b, c, k in zip(t.tau, t.lat, lon, t.kt)]
    members = {k: v for k, v in tracks.items() if re.fullmatch(r"A[PC]\d\d", k)}
    mean = tracks.get("AEMN")
    if mean is None and members:                                          # build a mean if the file had none
        taus = sorted({p[0] for v in members.values() for p in v})
        mean = []
        for tau in taus:
            pts = [p for v in members.values() for p in v if p[0] == tau]
            if len(pts) >= max(3, len(members) // 3):
                mean.append([tau, round(float(np.mean([p[1] for p in pts])), 2), round(float(np.mean([p[2] for p in pts])), 2), int(np.mean([p[3] for p in pts]))])
    spread = []
    if mean and members:
        for tau, la, lo, _ in mean:
            d = [_km((la, lo), (p[1], p[2])) for v in members.values() for p in v if p[0] == tau]
            if d: spread.append([tau, int(np.mean(d)), len(d)])
    cyc = g.cycle.max()
    return dict(cycle=cyc.strftime("%Y-%m-%dT%H:%MZ"), members=members, mean=mean, gfs=tracks.get("AVNO") or tracks.get("GFSO"),
                spread=spread, n_members=len(members))
