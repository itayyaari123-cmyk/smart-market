"""
סל חכם – סנכרון יומי מהמחשב הביתי.

מה זה עושה (כל יום, אוטומטית, דרך "מתזמן המשימות" של Windows):
  1. מוריד מחירים מכל הרשתות בכל הערים (כמו 2_update_prices.bat)
  2. אורז אותם לקובץ אחד לכל עיר
  3. מעלה אותם ל-GitHub (Release בשם home-data – מוחלף כל פעם, לא מנפח את המאגר)
  4. מבקש מ-GitHub לבנות את האתר מחדש עם הנתונים

כך האתר מקבל גם את הרשתות שחוסמות את השרתים של GitHub (ויקטורי, מחסני השוק, קרפור, סופר-פארם...).

הפעלה ידנית:   py -3.12 home_sync.py
התקנה:        setup_home_sync.bat  (שומר את הטוקן ב-Windows ויוצר משימה יומית)
"""
import datetime as dt
import getpass
import gzip
import json
import logging
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
LOG = BASE / "sync.log"
REPO = "itayyaari123-cmyk/smart-market"
RELEASE_TAG = "home-data"
WORKFLOW = "update.yml"
KEYRING_SERVICE = "smart-basket-github"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa
        pass


def log(msg):
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {msg}"
    try:
        print(line, flush=True)
    except Exception:  # noqa
        pass
    try:
        old = LOG.read_text(encoding="utf-8").splitlines()[-3000:] if LOG.exists() else []
        LOG.write_text("\n".join(old + [line]) + "\n", encoding="utf-8")
    except Exception:  # noqa
        pass


# ───────── טוקן (נשמר ב-Windows Credential Manager, לא בקובץ) ─────────
def get_token():
    import keyring
    return keyring.get_password(KEYRING_SERVICE, REPO)


def save_token(tok):
    import keyring
    keyring.set_password(KEYRING_SERVICE, REPO, tok)


def gh(method, url, token, **kw):
    import requests
    h = kw.pop("headers", {})
    h.update({"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
              "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "smart-basket-home-sync"})
    r = requests.request(method, url if url.startswith("http") else API + url, headers=h, timeout=300, **kw)
    return r


def check_token(token):
    r = gh("GET", f"/repos/{REPO}", token)
    if r.status_code != 200:
        return f"הטוקן לא עובד (קוד {r.status_code}). ודא שהוא מוגדר למאגר smart-market."
    return None


# ───────── ייצוא הנתונים ─────────
def build_contrib(db_path, out_dir):
    import core
    cfg = core.load_config()
    con = sqlite3.connect(db_path)
    con.executescript(core.SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(stores)")}
    first = core.cities(cfg)[0]["id"]
    excl = [core.norm(w) for w in cfg.get("exclude_store_words", [])]
    files = []
    out_dir.mkdir(exist_ok=True)
    for city in core.cities(cfg):
        cid = city["id"]
        products, index, stores = [], {}, []
        q = "SELECT chain,store_id,name,address,lat,lon,updated" + (",city_id" if "city_id" in cols else ",NULL") + " FROM stores"
        for chain, sid, name, addr, lat, lon, upd, scid in con.execute(q).fetchall():
            if chain not in cfg["chains"] or (scid or first) != cid or any(w in core.norm(name) for w in excl):
                continue
            flat, pflat, descs = core.export_store(con, chain, sid, products, index)
            stores.append({"chain": chain, "id": sid, "name": name, "address": addr, "stale": upd,
                           "lat": round(lat, 6) if lat else None, "lon": round(lon, 6) if lon else None,
                           "items": flat, "promos": pflat, "pd": descs})
        data = {"products": products, "stores": stores,
                "last_update": max((s["stale"] or "" for s in stores), default=None)}
        f = out_dir / f"{cid}.json.gz"
        f.write_bytes(gzip.compress(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 9))
        log(f"  {cid}: {len(stores)} סניפים -> {f.name} ({f.stat().st_size / 1e6:.1f}MB)")
        files.append(f)
    return files


# ───────── העלאה ל-GitHub ─────────
def upload(files, token):
    r = gh("GET", f"/repos/{REPO}/releases/tags/{RELEASE_TAG}", token)
    if r.status_code == 404:
        r = gh("POST", f"/repos/{REPO}/releases", token, json={
            "tag_name": RELEASE_TAG, "target_commitish": "main", "name": "נתונים מהמחשב הביתי",
            "body": "מתעדכן אוטומטית כל יום ע\"י home_sync.py. לא למחוק.", "prerelease": True})
    r.raise_for_status()
    rel = r.json()
    existing = {a["name"]: a["id"] for a in rel.get("assets", [])}
    for f in files:
        if f.name in existing:
            gh("DELETE", f"/repos/{REPO}/releases/assets/{existing[f.name]}", token).raise_for_status()
        for attempt in range(3):
            up = gh("POST", f"{UPLOADS}/repos/{REPO}/releases/{rel['id']}/assets?name={f.name}", token,
                    headers={"Content-Type": "application/gzip"}, data=f.read_bytes())
            if up.status_code in (200, 201):
                break
            log(f"  העלאת {f.name} נכשלה (קוד {up.status_code}), מנסה שוב...")
            time.sleep(5)
        else:
            raise RuntimeError(f"upload failed for {f.name}: {up.status_code} {up.text[:200]}")
    log(f"  הועלו {len(files)} קבצים ל-GitHub")
    d = gh("POST", f"/repos/{REPO}/actions/workflows/{WORKFLOW}/dispatches", token, json={"ref": "main"})
    if d.status_code == 204:
        log("  GitHub מתחיל לבנות את האתר מחדש (כ-30–60 דקות)")
    else:
        log(f"  לא הצלחתי להפעיל בנייה מחדש (קוד {d.status_code}) – האתר יתעדכן בהרצה הקבועה של הצהריים")


def setup():
    print("=" * 60)
    print(" Smart Basket - home sync setup")
    print("=" * 60)
    print("1) A browser page will open: GitHub -> Fine-grained token.")
    print("   - Token name: smart-basket-home   |  Expiration: 1 year (or no expiration)")
    print("   - Repository access: Only select repositories -> smart-market")
    print("   - Permissions -> Repository: Contents = Read and write, Actions = Read and write")
    print("   - Generate token, copy it.")
    print("2) Paste it here (it stays hidden) and press Enter.")
    import webbrowser
    webbrowser.open("https://github.com/settings/personal-access-tokens/new")
    tok = getpass.getpass("Token: ").strip()
    err = check_token(tok)
    if err:
        print("ERROR:", "The token does not work for smart-market. Check repository access and permissions.")
        sys.exit(1)
    save_token(tok)
    print("Token saved in Windows Credential Manager.")


def main():
    if "--setup" in sys.argv:
        return setup()
    log("=== סנכרון יומי התחיל ===")
    token = get_token()
    if not token:
        log("אין טוקן שמור – הרץ את setup_home_sync.bat")
        return 1
    err = check_token(token)
    if err:
        log(err)
        return 1
    import core
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("Logger").setLevel(logging.ERROR)
    t0 = time.time()
    core.update_all(lambda m: log("  " + str(m)) if ("סניף" not in str(m) or "שגיאה" in str(m)) else None)
    log(f"  ההורדה הסתיימה ({(time.time() - t0) / 60:.0f} דקות)")
    files = build_contrib(core.DB_PATH, BASE / "data" / "upload")
    upload(files, token)
    log("=== הסתיים בהצלחה ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa
        log("שגיאה: " + "".join(traceback.format_exception_only(type(e), e)).strip())
        log(traceback.format_exc())
        sys.exit(1)
