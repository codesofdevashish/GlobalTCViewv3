"""
Historical context from IBTrACS v04r01 (NOAA NCEI, public domain, no login), since 1980.
  analogs     past storms that passed within 300 km of a storm's current position within +-45 days of the year
  frequency   how often that happens, how many became hurricane-strength, how many intensified rapidly
  season      storms and ACE so far this season vs the 1991-2020 average to the same date, per basin
Winds are USA_WIND (1-minute, kt; JTWC/NHC), the same convention as TCVitals.
"""
import io, os
import numpy as np
import pandas as pd
import requests

URL = ("https://www.ncei.noaa.gov/data/international-best-track-archive-for-climate-stewardship-ibtracs/"
       "v04r01/access/csv/ibtracs.since1980.list.v04r01.csv")
OUR_TO_IB = {"L": "NA", "E": "EP", "C": "EP", "W": "WP", "A": "NI", "B": "NI", "S": "SI", "P": "SP", "U": "SP", "Q": "SA"}
IB_NAMES = {"NA": "North Atlantic", "EP": "East and Central Pacific", "WP": "West Pacific", "NI": "North Indian Ocean",
            "SI": "South Indian Ocean", "SP": "South Pacific", "SA": "South Atlantic"}


def load(cache):
    """IBTrACS track points (6-hourly) with 1-minute winds, cached as a small parquet-like pickle."""
    os.makedirs(cache, exist_ok=True)
    pk = os.path.join(cache, "ibtracs_since1980.pkl")
    if os.path.exists(pk) and (pd.Timestamp.now() - pd.Timestamp(os.path.getmtime(pk), unit="s")) < pd.Timedelta(days=4):
        return pd.read_pickle(pk)
    print("downloading IBTrACS (since 1980)...")
    r = requests.get(URL, timeout=600, headers={"User-Agent": "tc3d-site/1.0"}); r.raise_for_status()
    df = pd.read_csv(io.BytesIO(r.content), skiprows=[1], usecols=["SID", "SEASON", "BASIN", "NAME", "ISO_TIME", "LAT", "LON", "USA_WIND", "NATURE"],
                     keep_default_na=False, na_values=[" ", ""], low_memory=False)
    df["time"] = pd.to_datetime(df.ISO_TIME, errors="coerce")
    df = df[df.time.notna() & (df.time.dt.hour % 6 == 0) & (df.time.dt.minute == 0)]
    df["kt"] = pd.to_numeric(df.USA_WIND, errors="coerce")
    df["BASIN"] = df.BASIN.replace({"MM": "NA"}).fillna("NA")
    df = df[["SID", "SEASON", "BASIN", "NAME", "time", "LAT", "LON", "kt", "NATURE"]].rename(columns={"LAT": "lat", "LON": "lon"})
    df.to_pickle(pk)
    print(f"  IBTrACS: {df.SID.nunique()} storms, {len(df)} 6-hourly points")
    return df


def _km(lat1, lon1, lat2, lon2):
    p = np.deg2rad
    return 6371 * 2 * np.arcsin(np.sqrt(np.sin(p(lat2 - lat1) / 2) ** 2 + np.cos(p(lat1)) * np.cos(p(lat2)) * np.sin(p((lon2 - lon1 + 540) % 360 - 180) / 2) ** 2))


def storm_summaries(ib):
    g = ib.groupby("SID")
    s = pd.DataFrame(dict(name=g.NAME.first(), season=g.SEASON.first(), basin=g.BASIN.first(), start=g.time.min(), peak=g.kt.max()))
    k = ib.sort_values(["SID", "time"]).set_index("time")
    dv = k.groupby("SID").kt.apply(lambda x: (x - x.shift(4)).max())          # largest 24-h change (4 x 6 h)
    s["dv24"] = dv; s["ri"] = s.dv24 >= 30
    return s


def context_for(storm, ib, summ, radius_km=300, days=45):
    """Analogs and local frequency around a storm's latest position (dict for the website)."""
    if ib is None or not len(ib): return None
    lat, lon, t = storm["lat"], storm["lon"], pd.Timestamp(storm["last_seen"])
    doy = t.dayofyear
    near = ib[(np.abs(ib.lat - lat) < radius_km / 100) & (_km(lat, lon, ib.lat.values, ib.lon.values) <= radius_km)]
    dd = np.abs(((near.time.dt.dayofyear - doy + 182) % 365) - 182)
    near = near[(dd <= days) & (near.time.dt.year < t.year) & (near.kt >= 34)]
    sids = near.SID.unique()
    if not len(sids): return dict(radius_km=radius_km, days=days, n=0, analogs=[])
    S = summ.loc[sids]
    closest = near.assign(d=_km(lat, lon, near.lat.values, near.lon.values)).groupby("SID").d.min()
    S = S.assign(closest_km=closest).sort_values(["closest_km"])
    years = max(1, t.year - 1980)
    out = dict(radius_km=radius_km, days=days, years=f"1980-{t.year - 1}", n=int(len(S)), per_decade=round(len(S) / years * 10, 1),
               n_hurricane=int((S.peak >= 64).sum()), n_ri=int(S.ri.sum()), median_peak=int(S.peak.median()),
               rank_peak=int((S.peak > storm["peak_kt"]).sum()) + 1, analogs=[])
    for sid, r in S.head(8).iterrows():
        tr = ib[ib.SID == sid].sort_values("time")
        out["analogs"].append(dict(sid=sid, name=str(r["name"]).title(), year=int(r.season), peak=int(r.peak) if pd.notna(r.peak) else None,
                                   ri=bool(r.ri), closest_km=int(r.closest_km),
                                   track=[[round(float(a), 1), round(float(b), 1), int(k) if pd.notna(k) else 0] for a, b, k in zip(tr.lat, tr.lon, tr.kt)][::2]))
    return out


def season_vs_normal(ib, today=None):
    """Named storms (34 kt+) and ACE so far this season vs the 1991-2020 average to the same date, per basin."""
    if ib is None or not len(ib): return None
    today = pd.Timestamp(today or pd.Timestamp.now("UTC").tz_localize(None))
    out = {}
    for b, name in IB_NAMES.items():
        x = ib[ib.BASIN == b]
        if not len(x): continue
        south = b in ("SI", "SP", "SA")
        def season_of(t): return np.where(t.dt.month >= 7, t.dt.year + 1, t.dt.year) if south else t.dt.year
        x = x.assign(seas=season_of(x.time))
        start = lambda y: pd.Timestamp(year=y - 1, month=7, day=1) if south else pd.Timestamp(year=y, month=1, day=1)
        cur = int(season_of(pd.Series([today]))[0]); elapsed = today - start(cur)
        def stats(y):
            s0 = start(y); w = x[(x.seas == y) & (x.time <= s0 + elapsed)]
            named = w[w.kt >= 34].SID.nunique(); ace = float((w.kt[w.kt >= 34] ** 2).sum() / 1e4)
            return named, ace
        clim = [stats(y) for y in range(1991, 2021)]
        n_now, a_now = stats(cur)
        out[b] = dict(name=name, season=cur, named=n_now, ace=round(a_now, 1), named_normal=round(float(np.mean([c[0] for c in clim])), 1),
                      ace_normal=round(float(np.mean([c[1] for c in clim])), 1), to=today.strftime("%Y-%m-%d"),
                      note="IBTrACS lags real time by a few days; storms of the last days may be missing")
    return out
