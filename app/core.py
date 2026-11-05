"""
ליבת הבוט: הורדת קבצי המחירים (חוק המזון / שקיפות מחירים), שמירה ב-SQLite,
התאמת רשימת קניות למוצרים בכל סניף, ודירוג הסניפים לפי עלות הסל.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import glob
import json
import logging
import math
import os
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BASKET_DATA") or (BASE / "data"))
RAW = DATA / "raw"
DB_PATH = DATA / "prices.db"

log = logging.getLogger("basket")


# ───────────────────────────── הגדרות ─────────────────────────────

def load_config() -> dict:
    with open(BASE / "config.json", encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(BASE / "config.json", "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def load_items() -> dict:
    with open(BASE / "items.json", encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


# ───────────────────────────── טקסט ─────────────────────────────

_QUOTES = str.maketrans({"״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'", "`": "'"})


def norm(s: str | None) -> str:
    s = (s or "").translate(_QUOTES).lower()
    s = re.sub(r"[\-_/\\,;:()\[\]*+]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# ───────────────────────────── יחידות ─────────────────────────────
# כל כמות מנורמלת לאחד משני ממדים: "w" (גרם, ומ"ל נחשב כגרם) או "u" (יחידות)

_UNIT_PATTERNS = [
    ("kg", r'ק"?ג|קילו|קג|kg|ק\.ג'),
    ("ml", r'מ"?ל|מיליליטר|מל|ml|מ\.ל'),
    ("l", r"ליטר|ל'|lit|ltr|\bl\b|ל\b"),
    ("g", r"גרם|גר|ג'|gr|\bg\b|ג\b"),
    ("u", r"יח|יחידה|יחידות|unit|ea|זוג|מארז"),
]
_FACTOR = {"kg": (1000, "w"), "g": (1, "w"), "l": (1000, "w"), "ml": (1, "w"), "u": (1, "u")}


def parse_unit(text: str | None):
    t = norm(text)
    if not t:
        return None
    for key, pat in _UNIT_PATTERNS:
        if re.search(pat, t):
            return key
    return None


_NAME_QTY = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(ק\"?ג|קג|קילו|גרם|גר|ג'|ג\b|מ\"?ל|מל|ליטר|ל'|ל\b|יח|ml|kg|gr|g\b|l\b)"
)
_MULTIPACK = re.compile(r"(\d+)\s*[x×\*]\s*(\d+(?:[.,]\d+)?)\s*(גרם|גר|ג'|מ\"?ל|מל|ליטר|ל\b)")


def to_base(qty: float, unit_key: str):
    f, dim = _FACTOR[unit_key]
    return qty * f, dim


def quantity_of(name: str, qty_raw: str, unit_raw: str, weighted: bool):
    """מחזיר (כמות בבסיס, ממד) או None."""
    if weighted:
        return 1000.0, "w"  # מחיר שקול = מחיר לק"ג
    n = norm(name)
    m = _MULTIPACK.search(n)
    if m:
        k = parse_unit(m.group(3))
        if k:
            return to_base(int(m.group(1)) * float(m.group(2).replace(",", ".")), k)
    # כמות שכתובה בשם המוצר ("100 גרם") אמינה יותר משדות הכמות, שלפעמים שגויים
    m = _NAME_QTY.search(n)
    if m:
        k = parse_unit(m.group(2))
        if k:
            base = to_base(float(m.group(1).replace(",", ".")), k)
            if base[0] >= 10:
                return base
    try:
        q = float(str(qty_raw).replace(",", "."))
    except (TypeError, ValueError):
        q = 0.0
    k = parse_unit(unit_raw)
    if q > 0 and k:
        base = to_base(q, k)
        # סינון ערכים לא הגיוניים שמופיעים בחלק מהרשתות
        if base[1] == "u" or 1 <= base[0] <= 50000:
            return base
    m = _NAME_QTY.search(n)
    if m:
        k = parse_unit(m.group(2))
        if k:
            return to_base(float(m.group(1).replace(",", ".")), k)
    # יחידה לא ידועה ("לא ידוע") אבל יש כמות: עד 5 = ליטר/ק"ג, 20-5000 = גרם/מ"ל
    if q > 0 and k is None:
        if q <= 5:
            return q * 1000, "w"
        if 20 <= q <= 5000:
            return q, "w"
    return None


_COUNT = re.compile(r"(?<!\d)(\d{1,2})\s*(?:יח|יחידות|ביצים|ב'|\*)|[smlx](\d{1,2})(?!\d)|(?<!\d)(\d{1,2})(?!\d)")


def count_in_name(nname: str):
    m = _COUNT.search(nname)
    if not m:
        return None
    v = int(next(g for g in m.groups() if g))
    return v if 2 <= v <= 60 else None


# ───────────────────────────── DB ─────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores(
  chain TEXT, store_id INTEGER, chain_name TEXT, name TEXT, address TEXT, city TEXT,
  lat REAL, lon REAL, updated TEXT, PRIMARY KEY(chain, store_id));
CREATE TABLE IF NOT EXISTS items(
  chain TEXT, store_id INTEGER, code TEXT, name TEXT, nname TEXT, manufacturer TEXT,
  price REAL, qty TEXT, unit TEXT, weighted INTEGER, PRIMARY KEY(chain, store_id, code));
CREATE INDEX IF NOT EXISTS ix_items_store ON items(chain, store_id);
CREATE TABLE IF NOT EXISTS geocache(q TEXT PRIMARY KEY, lat REAL, lon REAL);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def db() -> sqlite3.Connection:
    DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(stores)")}
    if "city_id" not in cols:
        con.execute("ALTER TABLE stores ADD COLUMN city_id TEXT")
        con.commit()
    return con


# ───────────────────────────── קריאת XML ─────────────────────────────

def _records(path: str, kind: str):
    """עובר על הקובץ ומחזיר dict לכל רשומת סניף/מוצר. סובלני לשמות תגיות שונים בין רשתות."""
    from lxml import etree

    parser = etree.XMLParser(recover=True, huge_tree=True)
    try:
        tree = etree.parse(path, parser)
    except Exception as e:  # noqa
        log.warning("cannot parse %s: %s", path, e)
        return
    root = tree.getroot()
    if root is None:
        return
    for el in root.iter():
        if len(el) < 3:
            continue
        m = {}
        for c in el:
            if not isinstance(c.tag, str):
                continue
            m[c.tag.split("}")[-1].lower()] = (c.text or "").strip()
        if kind == "store" and "storeid" in m and ({"storename", "address", "city"} & m.keys()):
            yield m
        elif kind == "item" and "itemcode" in m and "itemprice" in m:
            yield m


def parse_stores(path):
    for m in _records(path, "store"):
        try:
            sid = int(re.sub(r"\D", "", m.get("storeid", "")) or 0)
        except ValueError:
            continue
        if sid <= 0:
            continue
        yield {
            "store_id": sid,
            "name": m.get("storename", ""),
            "address": m.get("address", ""),
            "city": m.get("city", ""),
            "chain_name": m.get("chainname", "") or m.get("subchainname", ""),
        }


def parse_items(path):
    for m in _records(path, "item"):
        name = m.get("itemname") or m.get("itemnm") or m.get("manufactureritemdescription") or ""
        try:
            price = float(m.get("itemprice", "0").replace(",", "."))
        except ValueError:
            continue
        if price <= 0 or not name:
            continue
        w = m.get("bisweighted", m.get("isweighted", "0")).strip().lower() in ("1", "true")
        yield {
            "code": m.get("itemcode", ""),
            "name": name,
            "manufacturer": m.get("manufacturername", ""),
            "price": price,
            "qty": m.get("quantity", ""),
            "unit": m.get("unitqty", "") or m.get("unitofmeasure", ""),
            "weighted": int(w),
        }


CHAIN_HE = {
    "SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "VICTORY_NEW_SOURCE": "ויקטורי", "YOHANANOF": "יוחננוף",
    "OSHER_AD": "אושר עד", "HAZI_HINAM": "חצי חינם", "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק",
    "YAYNO_BITAN_AND_CARREFOUR": "קרפור / יינות ביתן", "TIV_TAAM": "טיב טעם", "SUPER_PHARM": "סופר-פארם",
    "GOOD_PHARM": "גוד פארם", "ZOL_VEBEGADOL": "זול ובגדול", "SUPER_YUDA": "סופר יודה", "STOP_MARKET": "סטופ מרקט",
    "KESHET": "קשת טעמים", "CITY_MARKET_SHOPS": "סיטי מרקט", "FRESH_MARKET_AND_SUPER_DOSH": "פרשמרקט",
    "NETIV_HASED": "נתיב החסד", "SHUK_AHIR": "שוק העיר", "KING_STORE": "קינג סטור", "MAAYAN_2000": "מעיין 2000",
    "YELLOW": "יילו", "SUPER_SAPIR": "סופר ספיר", "WOLT": "וולט מרקט",
}


def cities(cfg: dict) -> list[dict]:
    """רשימת הערים שבהן אוספים מחירים. תואם גם להגדרות הישנות (עיר אחת)."""
    if cfg.get("cities"):
        return cfg["cities"]
    return [{"id": "beer-sheva", "name": "באר שבע", "names": cfg.get("city_names", []),
             "codes": cfg.get("city_codes", []), "bbox": [31.17, 31.33, 34.70, 34.90], "center": [31.2518, 34.7913]}]


def city_by_id(cfg: dict, cid: str | None) -> dict:
    cs = cities(cfg)
    return next((c for c in cs if c["id"] == cid), None) or next(
        (c for c in cs if c["id"] == cfg.get("default_city")), cs[0])


def city_of(store: dict, cfg: dict) -> str | None:
    """לאיזו עיר שייך הסניף (id), או None. סדר העדיפויות: קוד יישוב → שם העיר בשדה העיר → כתובת/שם הסניף.
    כתובת נבדקת רק כשאין עיר בשדה, כי "רחוב נס ציונה" קיים בהרבה ערים."""
    name = norm(store.get("name"))
    if any(norm(x) in name for x in cfg.get("exclude_store_words", [])):
        return None
    city = norm(store.get("city"))
    cs = cities(cfg)
    for c in cs:
        if city and city in {norm(x) for x in c.get("codes", [])}:
            return c["id"]
    if city.isdigit() and city != "0":
        return None  # קוד יישוב של עיר אחרת
    if city and city not in ("0", "unknown", "לא ידוע"):
        for c in cs:
            if any(norm(n) and (norm(n) == city or norm(n) in city) for n in c.get("names", [])):
                return c["id"]
        return None  # יש עיר בשדה – והיא לא אחת משלנו
    hay = norm(store.get("address")) + " " + name
    for c in cs:
        if any(norm(n) and norm(n) in hay for n in c.get("names", [])):
            return c["id"]
    return None


def is_in_city(store: dict, cfg: dict) -> bool:
    return city_of(store, cfg) is not None


# ───────────────────────────── הורדה ─────────────────────────────

def _xml_files(folder: Path):
    """כל הקבצים שהורדו ונראים כמו XML (חלק מהרשתות שומרות בלי סיומת)."""
    out = []
    for p in glob.glob(str(folder / "**" / "*"), recursive=True):
        if not os.path.isfile(p):
            continue
        with open(p, "rb") as f:
            head = f.read(64).lstrip(b"\xef\xbb\xbf \r\n\t")
        if head.startswith(b"<") or head[:2] in (b"\xff\xfe", b"\xfe\xff"):
            out.append(p)
    return sorted(out, key=os.path.getmtime)


async def _drain(gen):
    out = []
    async for r in gen:
        out.append(r)
    return out


# שמות חלופיים לרשתות בגרסאות שונות של הספרייה
_CHAIN_ALIASES = {
    "VICTORY_NEW_SOURCE": ["VICTORY"],
    "MAHSANI_ASHUK_NEW_SOURCE": ["MAHSANI_ASHUK"],
    "HET_COHEN_NEW_SOURCE": ["HET_COHEN"],
}


_IMPORT_ERR_SHOWN = False


def _scraper(chain_key: str, folder: Path):
    """יוצר את הסקרייפר. מתאים את עצמו לגרסה המותקנת של הספרייה (הממשק השתנה בין גרסאות)."""
    import inspect
    from il_supermarket_scarper import ScraperFactory
    global _IMPORT_ERR_SHOWN

    members = ScraperFactory.__members__
    name = next((n for n in [chain_key, *_CHAIN_ALIASES.get(chain_key, [])] if n in members), None)
    if name is None:
        raise KeyError(f"הרשת {chain_key} לא קיימת בגרסה המותקנת של הספרייה")
    cls = members[name].value
    params = inspect.signature(cls.__init__).parameters
    if "folder_name" in params:  # גרסאות 0.x
        return cls(folder_name=str(folder))
    if "file_output" in params:  # גרסאות 1.x
        try:
            try:
                from il_supermarket_scarper.utils.files.file_output import DiskFileOutput  # 1.0.19+
            except ImportError:
                from il_supermarket_scarper.utils.file_output import DiskFileOutput  # 1.0.0–1.0.8
            return cls(file_output=DiskFileOutput(storage_path=str(folder)))
        except Exception as e:  # noqa
            if not _IMPORT_ERR_SHOWN:
                log.warning("DiskFileOutput unavailable (%s: %s) – using default folder", type(e).__name__, e)
                _IMPORT_ERR_SHOWN = True
    return cls()  # תיקיית ברירת המחדל של הספרייה


def _storage_dir(s, fallback: Path) -> Path:
    try:
        p = s.get_storage_path()
        p = p if isinstance(p, (str, os.PathLike)) else getattr(p, "storage_path", None)
        return Path(os.path.abspath(p)) if p else fallback
    except Exception:  # noqa
        return fallback


def _download(chain_key: str, folder: Path, file_type: str, store_id=None):
    """מוריד קובץ אחד מהסוג המבוקש. כל הורדה מקבלת תיקייה נקייה – כולל מסד הסטטוס של הספרייה,
    שאחרת "זוכר" קבצים שכבר ירדו ומדלג עליהם בהרצה הבאה."""
    import inspect
    from il_supermarket_scarper import FileTypesFilters

    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
    files_dir = folder / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    s = _scraper(chain_key, files_dir)
    out = _storage_dir(s, files_dir)
    if out != files_dir:  # הספרייה כותבת לתיקייה משלה – מנקים אותה כדי לקרוא רק את הקבצים החדשים
        for f in glob.glob(str(out / "*")):
            if os.path.isfile(f):
                os.remove(f)
        out.mkdir(parents=True, exist_ok=True)
    kwargs = dict(files_types=[FileTypesFilters[file_type].name], limit=1, store_id=store_id)
    if "re_download" in inspect.signature(s.scrape).parameters:
        kwargs["re_download"] = None
    res = s.scrape(**kwargs)
    if inspect.isasyncgen(res):
        asyncio.run(_drain(res))
    elif inspect.iscoroutine(res):
        asyncio.run(res)
    return _xml_files(out)


def scraper_version() -> str:
    try:
        from importlib.metadata import version
        return version("il-supermarket-scraper")
    except Exception:  # noqa
        return "?"


def update_chain(chain_key: str, cfg: dict, con: sqlite3.Connection, report=print) -> int:
    """מוריד קובץ סניפים, מסנן סניפים בעיר, ומוריד מחירים מלאים לכל סניף. מחזיר מס' סניפים שעודכנו."""
    base = RAW / chain_key
    files = _download(chain_key, base / "stores", "STORE_FILE")
    stores = [s for f in files for s in parse_stores(f)]
    if not stores:
        report(f"  {chain_key}: לא הצלחתי להוריד את קובץ הסניפים (האתר של הרשת לא זמין או השתנה)")
        return 0
    local = {}
    for s_ in stores:
        cid = city_of(s_, cfg)
        if cid:
            local[s_["store_id"]] = {**s_, "city_id": cid}
    if not local:
        report(f"  {chain_key}: אין סניפים בערים שלנו (נבדקו {len(stores)} סניפים)")
        return 0
    now = dt.datetime.now().isoformat(timespec="minutes")
    done = 0
    for sid, st in local.items():
        try:
            pf = _download(chain_key, base / f"prices_{sid}", "PRICE_FULL_FILE", store_id=sid)
            rows = [it for f in pf for it in parse_items(f)]
        except Exception as e:  # noqa
            report(f"  {chain_key} סניף {sid}: שגיאה בהורדה ({e})")
            continue
        if not rows:
            report(f"  {chain_key} סניף {sid} ({st['name']}): לא נמצא קובץ מחירים")
            continue
        save_store_prices(con, chain_key, st, rows, now)
        done += 1
        report(f"  {chain_key} סניף {sid} ({st['name']}, {st['city_id']}): {len(rows):,} מוצרים")
    return done


def save_store_prices(con, chain_key, st, rows, now):
    con.execute(
        "INSERT INTO stores(chain,store_id,chain_name,name,address,city,updated,city_id) VALUES(?,?,?,?,?,?,?,?) "
        "ON CONFLICT(chain,store_id) DO UPDATE SET chain_name=excluded.chain_name,name=excluded.name,"
        "address=excluded.address,city=excluded.city,updated=excluded.updated,city_id=excluded.city_id",
        (chain_key, st["store_id"], st.get("chain_name", ""), st["name"], st["address"], st["city"], now,
         st.get("city_id")),
    )
    con.execute("DELETE FROM items WHERE chain=? AND store_id=?", (chain_key, st["store_id"]))
    con.executemany(
        "INSERT OR REPLACE INTO items VALUES(?,?,?,?,?,?,?,?,?,?)",
        [(chain_key, st["store_id"], r["code"], r["name"], norm(r["name"]), r["manufacturer"],
          r["price"], r["qty"], r["unit"], r["weighted"]) for r in rows],
    )
    con.commit()


def update_all(report=print) -> None:
    cfg = load_config()
    con = db()
    total = 0
    logging.getLogger("Logger").setLevel(logging.ERROR)  # הספרייה מדפיסה הרבה; משאירים רק שגיאות
    import il_supermarket_scarper as _m
    report(f"גרסת ספריית ההורדה: {scraper_version()} | {os.path.dirname(_m.__file__)}")
    for ck in cfg["chains"]:
        report(f"⏳ {ck}")
        try:
            total += update_chain(ck, cfg, con, report)
        except Exception as e:  # noqa
            report(f"  {ck}: נכשל ({type(e).__name__}: {e})")
    geocode_stores(con, report)
    con.execute("INSERT OR REPLACE INTO meta VALUES('last_update',?)",
                (dt.datetime.now().isoformat(timespec="minutes"),))
    con.commit()
    report(f"✅ עודכנו {total} סניפים")


# ───────────────────────────── מיקום ─────────────────────────────

# תיבה גסה סביב באר שבע – כל תוצאת איתור מחוץ לה נפסלת (מונע "גולומב" בתל אביב)
BBOX = (31.17, 31.33, 34.70, 34.90)


def _in_bbox(lat, lon, box=None):
    b = box or BBOX
    return b[0] <= lat <= b[1] and b[2] <= lon <= b[3]


def _nominatim(q):
    from geopy.geocoders import Nominatim
    import time
    time.sleep(1.1)  # מדיניות Nominatim: בקשה אחת לשנייה
    loc = Nominatim(user_agent="beersheva-basket-bot").geocode(q, country_codes="il", timeout=10)
    return (loc.latitude, loc.longitude) if loc else None


def _photon(q, box=None):
    import requests
    b = box or BBOX
    r = requests.get("https://photon.komoot.io/api/",
                     params={"q": q, "lat": (b[0] + b[1]) / 2, "lon": (b[2] + b[3]) / 2, "limit": 3},
                     timeout=10, headers={"User-Agent": "beersheva-basket-bot"})
    for f in r.json().get("features", []):
        lon, lat = f["geometry"]["coordinates"]
        if _in_bbox(lat, lon, b):
            return lat, lon
    return None


def geocode(q: str, con=None, bbox=True):
    con = con or db()
    row = con.execute("SELECT lat,lon FROM geocache WHERE q=?", (q,)).fetchone()
    if row and row[0] is not None:
        return row
    box = tuple(bbox) if isinstance(bbox, (list, tuple)) else None
    res, net_error = None, False
    for fn in (_nominatim, lambda x: _photon(x, box)):
        try:
            res = fn(q)
        except Exception as e:  # noqa
            log.warning("geocode failed for %s: %s", q, e)
            net_error = True
            continue
        if res and bbox and not _in_bbox(*res, box):
            res = None
        if res:
            break
    if res or not net_error:
        con.execute("INSERT OR REPLACE INTO geocache VALUES(?,?,?)", (q, *(res or (None, None))))
        con.commit()
    return res


def _clean_addr(a: str) -> str:
    a = re.sub(r"https?://\S*", " ", a or "")
    a = re.sub(r"\b(unknown|0)\b", " ", a)
    a = re.sub(r"(רח'|רחוב|שד'|שדרות|שד`|מרכז מסחרי|מרכז מסחרי ע|קומה\s*-?\d+|אזור תעשיה|א\.ת\.?)", " ", a)
    a = a.replace('ב"ש', "באר שבע").replace("ב'ש", "באר שבע")
    return re.sub(r"\s+", " ", a).strip(" ,-.")


def geocode_stores(con, report=print, retry_failed=True):
    """מאתר מיקום לסניפים בלי קואורדינטות. מנסה כמה ניסוחים של הכתובת ושני שירותי מפות חינמיים."""
    cfg = load_config()
    if retry_failed:
        con.execute("DELETE FROM geocache WHERE lat IS NULL")
    rows = con.execute("SELECT chain,store_id,address,name,city_id FROM stores WHERE lat IS NULL").fetchall()
    found = 0
    for chain, sid, addr, name, cid in rows:
        c = city_by_id(cfg, cid)
        city, box = c["name"], c.get("bbox")
        a, n = _clean_addr(addr), _clean_addr(name)
        n = re.sub(r"^\d+\s*", "", n)  # "58 סניף אורגים" -> "סניף אורגים"
        tries = []
        if a:
            tries += [f"{a}, {city}", a if city in a else None]
            m = re.match(r"(\d+)\s+(.+)", a)  # "101 אברהם אבינו" -> "אברהם אבינו 101"
            if m:
                tries.append(f"{m.group(2)} {m.group(1)}, {city}")
        if n:
            tries.append(f"{n}, {city}")
        res = None
        for q in [t for t in tries if t]:
            res = geocode(q, con, bbox=box or True)
            if res:
                break
        if res:
            found += 1
            con.execute("UPDATE stores SET lat=?,lon=? WHERE chain=? AND store_id=?", (*res, chain, sid))
            con.commit()
    if rows:
        report(f"📍 אותרו {found} מתוך {len(rows)} סניפים שלא היה להם מיקום")
    con.commit()


def home_location(cfg=None):
    cfg = cfg or load_config()
    if cfg.get("home_lat") and cfg.get("home_lon"):
        return cfg["home_lat"], cfg["home_lon"], False
    res = geocode(cfg["home_address"]) if cfg.get("home_address") else None
    if res:
        cfg["home_lat"], cfg["home_lon"] = res
        save_config(cfg)
        return res[0], res[1], False
    return cfg["fallback_lat"], cfg["fallback_lon"], True


def set_home(address: str = None, lat: float = None, lon: float = None) -> str:
    cfg = load_config()
    if lat is None:
        res = geocode(address)
        if not res:
            return f"לא הצלחתי למצוא את הכתובת \"{address}\". נסה בפורמט: רחוב מספר, עיר"
        lat, lon = res
    cfg.update(home_address=address or f"{lat:.5f},{lon:.5f}", home_lat=lat, home_lon=lon)
    save_config(cfg)
    return f"📍 המיקום עודכן: {cfg['home_address']}"


def haversine(a, b):
    R = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


# ───────────────────────────── התאמת מוצרים ─────────────────────────────

def _vocab(items: dict) -> dict:
    v = {}
    for key, spec in items.items():
        for c in [key, *spec.get("aliases", [])]:
            v[norm(c)] = key
    return v


def parse_list(text: str, items: dict | None = None) -> list[str]:
    """מפצל רשימה. מבין גם רשימה בלי פסיקים ("משחת שיניים ביצים חלב") בעזרת מילון המוצרים:
    מזהה את הצירוף הארוך ביותר שמוכר, ומילים לא מוכרות שנשארות הופכות לחיפוש חופשי."""
    items = items if items is not None else load_items()
    vocab = _vocab(items)
    maxlen = max((len(k.split()) for k in vocab), default=1)
    out = []
    for chunk in re.split(r"[,\n;،]+", text):
        chunk = re.sub(r"^\s*[-•*\d.)]+\s*", "", chunk).strip()
        words = norm(chunk).split()
        i, buf = 0, []
        while i < len(words):
            hit = None
            for j in range(min(len(words), i + maxlen), i, -1):
                ph = " ".join(words[i:j])
                if ph in vocab:
                    hit = (j, ph)
                    break
                if ph.startswith("ו") and ph[1:] in vocab:  # "וחלב"
                    hit = (j, ph[1:])
                    break
            if hit:
                if buf:
                    out.append(" ".join(buf))
                    buf = []
                out.append(hit[1])
                i = hit[0]
            else:
                buf.append(words[i])
                i += 1
        if buf:
            out.append(" ".join(buf))
    return out


_TERM_QTY = re.compile(
    r'(?:(\d+(?:[.,]\d+)?)\s*|(חצי|רבע)\s+)?'
    r'(ק"ג|קג|קילו|קילוגרם|קילוגרמים|גרם|גר|ג\'|ליטר|ליטרים|ל\'|מ"ל|מל|יחידות|יחידה|יח\'|יח)(?=\s|$)')
_STOP = {"של", "את", "עם", "בערך", "כ", "גם"}


def parse_term_qty(term: str):
    """'קילו חזה עוף' -> ('חזה עוף', (1000,'w'), 'קילו'). מחזיר target=None אם לא צוינה כמות."""
    t = norm(term)
    m = _TERM_QTY.search(t)
    if not m:
        return t, None, ""
    num = float(m.group(1).replace(",", ".")) if m.group(1) else {"חצי": 0.5, "רבע": 0.25}.get(m.group(2) or "", 1.0)
    u = m.group(3)
    if u in ("יחידות", "יחידה", "יח'", "יח"):
        target = (num, "u")
    elif u in ("גרם", "גר", "ג'", "מ\"ל", "מל"):
        target = (num, "w")
    else:
        target = (num * 1000, "w")
    clean = (t[:m.start()] + " " + t[m.end():]).strip()
    clean = re.sub(r"\s+", " ", clean)
    return clean or t, target, m.group(0).strip()


def resolve(term: str, items: dict) -> tuple[str, dict, bool]:
    """מחזיר (שם תצוגה, מפרט חיפוש, האם מהמילון). מבין כמות בטקסט ('2 קילו חזה עוף')."""
    t, qty, qty_txt = parse_term_qty(term)
    disp_suffix = f" ({qty_txt})" if qty_txt else ""

    def with_qty(key, spec):
        if qty:
            spec = dict(spec)
            tgt = spec.get("target")
            if not tgt or tgt[1] == qty[1] or qty[1] == "w":
                spec["target"] = list(qty)
        return key + disp_suffix, spec, True

    for key, spec in items.items():
        if t == norm(key) or t in (norm(a) for a in spec.get("aliases", [])):
            return with_qty(key, spec)
    # התאמה חלקית: מה שהוקלד מכיל מוצר מהמילון (למשל "חזה עוף טרי" -> "חזה עוף")
    best = None
    for key, spec in items.items():
        for cand in [key, *spec.get("aliases", [])]:
            c = norm(cand)
            if c and (re.search(rf"(^|\s){re.escape(c)}(\s|$)", t) or (len(t) >= 3 and t in c)):
                if best is None or len(c) > best[0]:
                    best = (len(c), key, spec)
    if best:
        return with_qty(best[1], best[2])
    words = [w for w in t.split() if w not in _STOP]
    return (term.strip(), {"words": words, "target": list(qty) if qty else None, "free": True}, False)


def matches(nname: str, spec: dict) -> bool:
    def words(k):
        return [w for w in (norm(x) for x in spec.get(k, [])) if w]
    if spec.get("starts") and not any(nname.startswith(s) for s in words("starts")):
        return False
    if spec.get("any") and not any(s in nname for s in words("any")):
        return False
    if spec.get("all") and not all(s in nname for s in words("all")):
        return False
    if any(s in nname for s in words("none")):
        return False
    if spec.get("words"):  # חיפוש חופשי: כל מילה כמילה שלמה ("חזה" לא יתאים ל"לחזה")
        for w in spec["words"]:
            if not re.search(rf"(^|[\s.'\"%]){re.escape(norm(w))}($|[\s.'\"%])", nname):
                return False
    return True


@dataclass
class Pick:
    name: str
    price: float          # מחיר מדף של המוצר שנבחר
    norm_cost: float      # עלות מנורמלת לכמות היעד (להשוואה הוגנת)
    size: str             # תיאור גודל
    weighted: bool
    target_label: str = ""


def _label(target):
    if not target:
        return ""
    a, d = target
    if d == "u":
        return f"{a:g} יח'"
    return f"{a/1000:g} ק\"ג/ליטר" if a >= 1000 else f"{a:g} גרם"


def _sql_prefilter(spec: dict):
    """תנאי LIKE ראשוני (מאיץ מאוד): OR על כל מילות starts/any, או המילה הנדירה מ-all."""
    for k in ("starts", "any"):
        if spec.get(k):
            return [norm(w) for w in spec[k] if norm(w)]
    if spec.get("all"):
        return [max((norm(w) for w in spec["all"]), key=len)]
    if spec.get("words"):
        return [max((norm(w) for w in spec["words"]), key=len)]
    return []


_GLOBAL_NONE = [norm(w) for w in ["קופון", "הנחה", "פיקדון", "פקדון", "מתנה", "לחתול", "לכלב", "לחתלתול", "פרמיו", "פריסקיז"]]


def best_in_store(con, chain, sid, spec) -> Pick | None:
    likes = [f"%{w}%" for w in _sql_prefilter(spec)]
    q = " OR ".join(["nname LIKE ?"] * len(likes)) or "1"
    rows = con.execute(f"SELECT name,nname,price,qty,unit,weighted FROM items WHERE chain=? AND store_id=? "
                       f"AND ({q})", (chain, sid, *likes)).fetchall()
    return best_from_rows(rows, spec)


def candidates_all_stores(con, spec) -> dict:
    """שאילתה אחת לכל המאגר – מחזיר {(chain, store_id): rows}."""
    likes = [f"%{w}%" for w in _sql_prefilter(spec)]
    q = " OR ".join(["nname LIKE ?"] * len(likes)) or "1"
    out = {}
    for row in con.execute(f"SELECT chain,store_id,name,nname,price,qty,unit,weighted FROM items WHERE ({q})", likes):
        out.setdefault((row[0], row[1]), []).append(row[2:])
    return out


_FREE_NONE = ["תערובת תיבול", "תיבול ל", "כריך", "בטעם", "טעם של", "מזון ל", "לחתול", "לכלב"]


def infer_target(cand: dict, spec: dict):
    """חיפוש חופשי בלי כמות: כמות ההשוואה = הגודל השכיח (חציון) של המוצרים שנמצאו, כדי לא להשוות
    אריזה של 100 גרם מול אריזה של 2 ק"ג."""
    sizes = {"w": [], "u": []}
    for rows in cand.values():
        for name, nname, price, qty, unit, weighted in rows:
            if price < 1 or not matches(nname, spec):
                continue
            q = quantity_of(name, qty, unit, bool(weighted))
            if q:
                sizes[q[1]].append(q[0])
    dim = "w" if len(sizes["w"]) >= len(sizes["u"]) else "u"
    vals = sorted(sizes[dim])
    if not vals:
        return None
    med = vals[len(vals) // 2]
    if dim == "u":
        return [max(1, round(med)), "u"]
    return [round(med, -1) if med >= 100 else med, "w"]


def best_from_rows(rows, spec) -> Pick | None:
    target = spec.get("target")
    min_price = spec.get("min_price", 1.0)
    best: Pick | None = None
    for name, nname, price, qty, unit, weighted in rows:
        if price < min_price or not matches(nname, spec):
            continue
        if any(w in nname for w in _GLOBAL_NONE):
            continue
        if spec.get("free") and any(norm(w) in nname and norm(w) not in " ".join(spec["words"]) for w in _FREE_NONE):
            continue
        weighted = bool(weighted)
        if spec.get("need") and not weighted and not any(norm(w) in nname for w in spec["need"]):
            continue  # למשל סלמון: שקיל, או מילה כמו "קפוא"
        qd = quantity_of(name, qty, unit, weighted)
        cost = price
        if target:
            t_amount, t_dim = target
            amount = None
            if t_dim == "u":
                if qd and qd[1] == "u" and qd[0] >= 2:
                    amount = qd[0]
                else:
                    amount = count_in_name(nname) if t_amount > 1 else 1
            elif qd and qd[1] == "w":
                amount = qd[0]
            if not amount:
                continue  # אי אפשר לדעת גודל – לא משווים (מונע "זול" מזויף)
            ratio = amount / t_amount
            if not weighted and not (0.4 <= ratio <= 2.5):
                continue
            cost = price / ratio
            if weighted:
                size = "שקיל, לק\"ג"
            elif t_dim == "u":
                size = f"{amount:g} יח'"
            else:
                size = f"{amount/1000:g} ק\"ג/ליטר" if amount >= 1000 else f"{amount:g} גרם/מ\"ל"
        else:
            size = ""
        if best is None or cost < best.norm_cost:
            best = Pick(name, price, cost, size, weighted, _label(target) if abs(cost - price) > 0.01 else "")
    return best


@dataclass
class StoreResult:
    chain: str
    store_id: int
    title: str
    address: str
    dist: float | None
    picks: dict = field(default_factory=dict)  # display -> Pick|None

    @property
    def found(self):
        return sum(1 for p in self.picks.values() if p)

    @property
    def total(self):
        return sum(p.norm_cost for p in self.picks.values() if p)


def compare_data(text: str = None, location=None, terms: list | None = None,
                 sort: str = "price", max_km: float | None = None, city: str | None = None) -> dict:
    """השוואת סל – תוצאה מובנית (משמשת את אפליקציית הווב, הצ'אט וטלגרם)."""
    cfg = load_config()
    items = load_items()
    con = db()
    if terms is None:
        terms = parse_list(text or "", items)
    terms = [t for t in (x.strip() for x in terms) if t]
    if not terms:
        return {"error": "לא זיהיתי מוצרים. כתוב רשימה מופרדת בפסיקים או בשורות."}
    resolved, seen = [], set()
    for t in terms:
        r = resolve(t, items)
        if r[0] not in seen:
            seen.add(r[0])
            resolved.append(r)

    if location is None:
        lat, lon, approx = home_location(cfg)
    else:
        lat, lon, approx = location[0], location[1], False
    city_id = city_by_id(cfg, city)["id"]
    first_city = cities(cfg)[0]["id"]
    stores = [r[:7] for r in con.execute(
        "SELECT chain,store_id,chain_name,name,address,lat,lon,city_id FROM stores").fetchall()
        if (r[7] or first_city) == city_id]
    if not stores:
        return {"error": "אין עדיין נתוני מחירים. לחץ על \"עדכון מחירים\"."}

    results, unknown_dist = [], 0
    for chain, sid, cname, sname, addr, slat, slon in stores:
        if chain not in cfg["chains"] or any(norm(x) in norm(sname) for x in cfg.get("exclude_store_words", [])):
            continue
        d = haversine((lat, lon), (slat, slon)) if slat else None
        limit = max_km or cfg["radius_km"]
        if d is not None and d > limit:
            continue
        if max_km and d is None:
            unknown_dist += 1
            continue
        results.append(StoreResult(chain, sid, sname, addr, d))
    for i, (disp, spec, known) in enumerate(resolved):
        cand = candidates_all_stores(con, spec)
        if spec.get("free") and not spec.get("target"):
            spec = {**spec, "target": infer_target(cand, spec)}
            resolved[i] = (disp, spec, known)
        for r in results:
            r.picks[disp] = best_from_rows(cand.get((r.chain, r.store_id), []), spec)
    if not results:
        return {"error": f"אין סניפים במרחק של עד {max_km:g} ק\"מ ממך. נסה להגדיל את המרחק." if max_km
                else "לא נמצאו סניפים ברדיוס שהוגדר."}

    cheapest = {}
    for disp, _, _ in resolved:
        cands = [(r.picks[disp].norm_cost, r) for r in results if r.picks.get(disp)]
        cheapest[disp] = min(cands, key=lambda x: x[0]) if cands else None

    def effective(r):
        return r.total + sum(cheapest[d][0] for d, p in r.picks.items() if not p and cheapest[d])

    # דירוג: עלות כוללת (מוצר חסר = הזול ביותר במקום אחר) + "קנס" קטן על כל מוצר חסר (עוד עצירה).
    # סניפים שחסרה בהם רוב הרשימה (למשל פארם) לא נכנסים לדירוג הראשי.
    anywhere = sum(1 for v in cheapest.values() if v) or 1
    penalty = cfg.get("missing_item_penalty", 5)
    min_found = max(1, math.ceil(anywhere * cfg.get("min_coverage", 0.6)))
    missing_n = lambda r: sum(1 for d, p in r.picks.items() if not p and cheapest[d])
    results.sort(key=lambda r: (r.found < min_found, effective(r) + penalty * missing_n(r)))
    last = (con.execute("SELECT v FROM meta WHERE k='last_update'").fetchone() or [None])[0]
    # הסניף הטוב ביותר מכל רשת – כדי לראות השוואה בין רשתות ולא 7 סניפים של אותה רשת
    per_chain, branches = [], {}
    for r in results:
        branches[r.chain] = branches.get(r.chain, 0) + 1
    if sort == "distance":
        # הסניף הקרוב ביותר מכל רשת (שיש בו רוב הרשימה), מהקרוב לרחוק
        near = sorted([r for r in results if r.found >= min_found],
                      key=lambda r: (r.dist is None, r.dist if r.dist is not None else 0, effective(r)))
        seen_chain = set()
        for r in near:
            if r.chain not in seen_chain:
                seen_chain.add(r.chain)
                per_chain.append(r)
    else:
        seen_chain = set()
        for r in results:
            if r.chain not in seen_chain and r.found >= min_found:
                seen_chain.add(r.chain)
                per_chain.append(r)
    if not per_chain:
        per_chain = results[:5]

    def pick_json(disp, p, known):
        if not p:
            return {"item": disp, "found": False, "free_search": not known}
        return {"item": disp, "found": True, "free_search": not known, "product": p.name, "size": p.size,
                "shelf_price": round(p.price, 2), "cost": round(p.norm_cost, 2),
                "per": p.target_label if p.target_label and abs(p.price - p.norm_cost) > 0.01 else ""}

    def store_json(r):
        return {
            "chain": r.chain, "chain_name": CHAIN_HE.get(r.chain, r.chain), "store": r.title,
            "address": r.address, "distance_km": round(r.dist, 1) if r.dist is not None else None,
            "total": round(effective(r), 2), "basket_here": round(r.total, 2),
            "branches": branches.get(r.chain, 1),
            "found": [d for d, _, _ in resolved if r.picks.get(d)],
            "missing": [d for d, _, _ in resolved if not r.picks.get(d) and cheapest[d]],
            "picks": [pick_json(d, r.picks.get(d), k) for d, _, k in resolved],
        }

    split = []
    for disp, _, known in resolved:
        v = cheapest[disp]
        if v:
            c, r = v
            split.append({**pick_json(disp, r.picks[disp], known), "chain": r.chain, "chain_name": CHAIN_HE.get(r.chain, r.chain),
                          "store": r.title})
        else:
            split.append({"item": disp, "found": False, "free_search": not known})
    best = results[0]
    split_total = round(sum(v[0] for v in cheapest.values() if v), 2)
    return {
        "items": [d for d, _, _ in resolved],
        "not_found_anywhere": [d for d, _, _ in resolved if not cheapest[d]],
        "stores_checked": len(results), "last_update": last, "approx_location": approx,
        "sort": sort, "max_km": max_km, "unknown_distance_excluded": unknown_dist,
        "cheapest_total": round(min(effective(r) for r in per_chain), 2),
        "ranking": [store_json(r) for r in per_chain],
        "split": {"total": split_total, "saving": round(max(effective(best) - split_total, 0), 2), "items": split},
    }


def compare(text: str, location=None) -> str:
    """גרסת טקסט (לטלגרם ולשורת הפקודה)."""
    d = compare_data(text, location)
    if "error" in d:
        return d["error"]
    L = [f"🛒 {len(d['items'])} מוצרים | {d['stores_checked']} סניפים | נתונים מ-{d['last_update']}", ""]
    for i, r in enumerate(d["ranking"][:5], 1):
        dist = f"{r['distance_km']} ק\"מ" if r["distance_km"] is not None else "מרחק לא ידוע"
        miss = f" (חסר: {', '.join(r['missing'])})" if r["missing"] else ""
        L.append(f"{i}. {r['chain_name']} {r['store']} ({dist}) – ₪{r['total']:,.2f}")
        L.append(f"   יש: {', '.join(r['found'])}{miss}")
    b = d["ranking"][0]
    L += ["", f"📋 פירוט – {b['chain_name']} {b['store']}:"]
    for p in b["picks"]:
        L.append(f"• {p['item']}: {p['product']} [{p['size']}] – ₪{p['cost']:,.2f}" if p["found"]
                 else f"• {p['item']}: ❌ אין בסניף")
    L += ["", f"🔀 סל מפוצל: ₪{d['split']['total']:,.2f} (חיסכון ₪{d['split']['saving']:,.2f})"]
    for p in d["split"]["items"]:
        L.append(f"• {p['item']}: ₪{p['cost']:,.2f} ב{p['chain_name']} {p['store']}" if p["found"]
                 else f"• {p['item']}: לא נמצא")
    return "\n".join(L)


def list_stores() -> str:
    con = db()
    lat, lon, _ = home_location()
    rows = con.execute("SELECT chain,chain_name,name,address,lat,lon,updated FROM stores").fetchall()
    if not rows:
        return "אין סניפים במאגר עדיין."
    out = []
    for chain, cname, name, addr, slat, slon, upd in rows:
        d = haversine((lat, lon), (slat, slon)) if slat else None
        dist = "%.1f ק\"מ" % d if d is not None else "מרחק לא ידוע"
        out.append((d if d is not None else 999, f"{CHAIN_HE.get(chain, cname or chain)} – {name}, {addr} | {dist} | עודכן {upd}"))
    return "\n".join(s for _, s in sorted(out))
