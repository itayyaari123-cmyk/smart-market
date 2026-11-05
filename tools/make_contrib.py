"""מייצא את המחירים מהמאגר של המחשב הביתי לקבצי contrib/<city>.json.gz, כגיבוי לרשתות ש-GitHub לא מצליח להוריד.
שימוש: python tools/make_contrib.py <path/to/prices.db>"""
import gzip
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import core  # noqa: E402


def main(db_path):
    cfg = core.load_config()
    con = sqlite3.connect(db_path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(stores)")}
    first = core.cities(cfg)[0]["id"]
    out = ROOT / "contrib"
    out.mkdir(exist_ok=True)
    for city in core.cities(cfg):
        cid = city["id"]
        products, index, stores = [], {}, []
        q = "SELECT chain,store_id,name,address,lat,lon,updated" + (",city_id" if "city_id" in cols else ",NULL") + " FROM stores"
        for chain, sid, name, addr, lat, lon, upd, scid in con.execute(q):
            if chain not in cfg["chains"] or (scid or first) != cid:
                continue
            flat = []
            for iname, price, qty, unit, w in con.execute(
                    "SELECT name,price,qty,unit,weighted FROM items WHERE chain=? AND store_id=?", (chain, sid)):
                qd = core.quantity_of(iname, qty, unit, bool(w))
                key = (iname, qd, int(w))
                pid = index.get(key)
                if pid is None:
                    pid = index[key] = len(products)
                    products.append([iname, round(qd[0], 2) if qd else 0, qd[1] if qd else "", int(w)])
                flat += [pid, round(price, 2)]
            stores.append({"chain": chain, "id": sid, "name": name, "address": addr, "stale": upd,
                           "lat": round(lat, 6) if lat else None, "lon": round(lon, 6) if lon else None, "items": flat})
        data = {"products": products, "stores": stores,
                "last_update": max((s["stale"] or "" for s in stores), default=None)}
        f = out / f"{cid}.json.gz"
        f.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9))
        print(f"{cid}: {len(stores)} stores -> {f.name} ({f.stat().st_size/1e6:.1f} MB)")


if __name__ == "__main__":
    main(sys.argv[1])
