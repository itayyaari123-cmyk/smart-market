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


def load_previous(city_id):
    """המחירים שפורסמו אתמול לעיר הזו. משמש כגיבוי לרשת שההורדה שלה נכשלה היום."""
    base = os.environ.get("PREVIOUS_DATA_URL", "").rsplit("/", 1)[0]
    if base:
        import requests
        names = [f"{city_id}.json"] + (["prices.json"] if city_id == "beer-sheva" else [])
        for name in names:
            try:
                r = requests.get(f"{base}/{name}", timeout=60)
                if r.ok:
                    print(f"[{city_id}] previous data: {base}/{name}")
                    return r.json()
            except Exception as e:  # noqa
                print(f"[{city_id}] previous data unavailable ({name}): {e}")
    for seed in (ROOT / "seed" / f"{city_id}.json", ROOT / "seed" / "prices.json" if city_id == "beer-sheva" else None):
        if seed and seed.exists():
            print(f"[{city_id}] using seed {seed.name}")
            return json.loads(seed.read_text(encoding="utf-8"))
    return None


def export_city(con, cfg, city):
    cid = city["id"]
    first = core.cities(cfg)[0]["id"]
    products, index, stores = [], {}, []
    for chain, sid, cname, name, addr, lat, lon, scid in con.execute(
            "SELECT chain,store_id,chain_name,name,address,lat,lon,city_id FROM stores ORDER BY rowid"):
        if chain not in cfg["chains"] or (scid or first) != cid:
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
    prev = load_previous(cid)
    fresh = {(s["chain"], s["id"]) for s in stores}
    fresh_chains = {s["chain"] for s in stores}
    carried = []
    if prev:
        for s in prev.get("stores", []):
            if s["chain"] in cfg["chains"] and s["chain"] not in fresh_chains and (s["chain"], s["id"]) not in fresh:
                flat, it = [], s["items"]
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
        print(f"[{cid}] carried over from previous data:", sorted(set(carried)))
    last = (con.execute("SELECT v FROM meta WHERE k='last_update'").fetchone() or [None])[0]
    keep = ["chains", "radius_km", "exclude_store_words", "missing_item_penalty", "min_coverage"]
    conf = {k: cfg.get(k) for k in keep}
    conf.update(home_lat=city["center"][0], home_lon=city["center"][1], city_id=cid, city_name=city["name"],
                bbox=city.get("bbox"))
    data = {
        "version": 2, "last_update": last, "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "config": conf, "chain_names": core.CHAIN_HE, "items": core.load_items(),
        "products": products, "stores": stores,
    }
    out = OUT.parent / f"{cid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"[{cid}] exported {len(stores)} stores, {len(products):,} products, {out.stat().st_size/1e6:.1f} MB")
    return {"id": cid, "name": city["name"], "center": city["center"], "bbox": city.get("bbox"),
            "stores": len(stores), "chains": len({s["chain"] for s in stores}),
            "stale_stores": sum(1 for s in stores if s.get("stale")), "file": out.name, "last_update": last}


def export(con):
    cfg = core.load_config()
    index = [export_city(con, cfg, c) for c in core.cities(cfg)]
    (OUT.parent / "cities.json").write_text(json.dumps({"default": cfg.get("default_city") or index[0]["id"],
                                                         "cities": index}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
    # תאימות לאחור: הכתובת הישנה של באר שבע ממשיכה לעבוד
    bs = OUT.parent / "beer-sheva.json"
    if bs.exists():
        OUT.write_bytes(bs.read_bytes())
    return sum(c["stores"] for c in index)


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
