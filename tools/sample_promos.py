import gzip, shutil, sys, logging
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import core
logging.getLogger("Logger").setLevel(logging.ERROR)
out = ROOT / "samples"; out.mkdir(exist_ok=True)
for chain, sid in [("RAMI_LEVY", 27), ("KESHET", 18), ("YOHANANOF", 40), ("KING_STORE", 8), ("SHUFERSAL", 35)]:
    for ft in ("PRICE_FULL_FILE", "PROMO_FULL_FILE"):
        try:
            files = core._download(chain, core.RAW / chain / f"{ft}_{sid}", ft, store_id=sid)
            for f in files[:1]:
                dst = out / f"{chain}__{sid}__{ft}.xml.gz"
                with open(f, "rb") as a, gzip.open(dst, "wb", 9) as b:
                    shutil.copyfileobj(a, b)
                print("saved", dst.name, dst.stat().st_size, flush=True)
        except Exception as e:
            print("FAILED", chain, ft, e, flush=True)
