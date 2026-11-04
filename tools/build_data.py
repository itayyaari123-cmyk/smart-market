"""נבנה ע"י GitHub Actions כל יום:
  1. מוריד את קבצי המחירים של סניפי באר שבע (core.update_all)
  2. מאתר מיקום לסניפים (עם מטמון geo.json שנשמר במאגר, כדי לא לאתר מחדש כל יום)
  3. מייצא את הכל לקובץ אחד site/data/prices.json שהאתר טוען
שימוש:  python tools/build_data.py            (הורדה + ייצוא)
        python tools/build_data.py --export   (רק ייצוא ממאגר קיים)"""
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import core  # noqa: E402

GEO = ROOT / "geo.json"
OUT = ROOT / "site" / "data" / "prices.json"


def seed_geo(con):
    if not GEO.exists():
        return
    geo = json.loads(GEO.read_text(encoding="utf-8"))
    for key, (lat, lon) in geo.items():
        chain, sid = key.split("|")
        con.execute("UPDATE stores SET lat=?,lon=? WHERE chain=? AND store_id=? AND lat IS NULL", (lat, lon, chain, int(sid)))
    con.commit()


def save_geo(con):
    geo = json.loads(GEO.read_text(encoding="utf-8")) if GEO.exists() else {}
    for chain, sid, lat, lon in con.execute("SELECT chain,store_id,lat,lon FROM stores WHERE lat IS NOT NULL"):
        geo[f"{chain}|{sid}"] = [round(lat, 6), round(lon, 6)]
    GEO.write_text(json.dumps(geo, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")


def load_previous():
    """המחירים שפורסמו אתמול (או קובץ ההתחלה). משמש כגיבוי לרשת שההורדה שלה נכשלה היום."""
    url = os.environ.get("PREVIOUS_DATA_URL")
    if url:
        try:
            import requests
            r = requests.get(url, timeout=60)
            if r.ok:
                print("loaded previous data from", url)
                return r.json()
        except Exception as e:  # noqa
            print("previous data unavailable:", e)
    seed = ROOT / "seed" / "prices.json"
    if seed.exists():
        print("using seed data as fallback")
        return json.loads(seed.read_text(encoding="utf-8"))
    return None


def export(con):
    cfg = core.load_config()
    products, index, stores = [], {}, []
    for chain, sid, cname, name, addr, lat, lon in con.execute(
            "SELECT chain,store_id,chain_name,name,address,lat,lon FROM stores ORDER BY rowid"):
        if chain not in cfg["chains"]:
            continue
        flat = []
        for iname, price, qty, unit, w in con.execute(
                "SELECT name,price,qty,unit,weighted FROM items WHERE chain=? AND store_id=? ORDER BY rowid", (chain, sid)):
            qd = core.quantity_of(iname, qty, unit, bool(w))
            key = (iname, qd, int(w))
            pid = index.get(key)
            if pid is None:
                pid = index[key] = len(products)
                products.append([iname, round(qd[0], 2) if qd else 0, qd[1] if qd else "", int(w)])
            flat += [pid, round(price, 2)]
        stores.append({"chain": chain, "id": sid, "name": name, "address": addr,
                       "lat": round(lat, 6) if lat else None, "lon": round(lon, 6) if lon else None, "items": flat})
    # רשת שלא ירדה היום (אתר תקול/חסום) – משאירים את המחירים הקודמים שלה, עם סימון
    prev = load_previous()
    fresh_chains = {s["chain"] for s in stores}
    carried = []
    if prev:
        for s in prev.get("stores", []):
            if s["chain"] in cfg["chains"] and s["chain"] not in fresh_chains:
                flat = []
                it = s["items"]
                for i in range(0, len(it), 2):
                    pname, amount, dim, w = prev["products"][it[i]]
                    key = (pname, (amount, dim) if dim else None, int(w))
                    pid = index.get(key)
                    if pid is None:
                        pid = index[key] = len(products)
                        products.append([pname, amount, dim, int(w)])
                    flat += [pid, it[i + 1]]
                stores.append({**s, "items": flat, "stale": s.get("stale") or prev.get("last_update")})
                carried.append(s["chain"])
    if carried:
        print("carried over from previous data:", sorted(set(carried)))
    last = (con.execute("SELECT v FROM meta WHERE k='last_update'").fetchone() or [None])[0]
    keep = ["chains", "radius_km", "exclude_store_words", "missing_item_penalty", "min_coverage", "home_lat", "home_lon",
            "city_names"]
    data = {
        "version": 1, "last_update": last, "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "config": {k: cfg.get(k) for k in keep}, "chain_names": core.CHAIN_HE, "items": core.load_items(),
        "products": products, "stores": stores,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"exported {len(stores)} stores, {len(products):,} products, {OUT.stat().st_size/1e6:.1f} MB")
    return len(stores)


def main():
    con = core.db()
    if "--export" not in sys.argv:
        orig = core.geocode_stores
        core.geocode_stores = lambda c, report=print, retry_failed=True: (seed_geo(c), orig(c, report, retry_failed))
        core.update_all(print)
        save_geo(con)
    n = export(con)
    if n < 10:
        sys.exit(f"only {n} stores exported – refusing to publish a broken site")


if __name__ == "__main__":
    main()
