import collections, random, sys, logging
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
import core
from lxml import etree
logging.getLogger("Logger").setLevel(logging.ERROR)
SAMPLES = [("RAMI_LEVY", 27), ("YOHANANOF", 40), ("OSHER_AD", 13), ("KING_STORE", 8), ("GOOD_PHARM", 702),
           ("TIV_TAAM", 15), ("KESHET", 18), ("MAAYAN_2000", 46), ("FRESH_MARKET_AND_SUPER_DOSH", 24), ("SHUFERSAL", 35)]
random.seed(1)
def low(el): return el.tag.split("}")[-1].lower() if isinstance(el.tag, str) else ""
for chain, sid in SAMPLES:
    print(f"\n######## {chain} store {sid}", flush=True)
    try:
        pf = core._download(chain, core.RAW / chain / f"s_prices_{sid}", "PRICE_FULL_FILE", store_id=sid)
        prices = {}
        for f in pf:
            for m in core._records(f, "item"):
                prices[m.get("itemcode")] = (m.get("itemname") or m.get("manufactureritemdescription"), m.get("itemprice"), m.get("quantity"), m.get("unitqty"), m.get("bisweighted"))
        files = core._download(chain, core.RAW / chain / f"s_promo_{sid}", "PROMO_FULL_FILE", store_id=sid)
    except Exception as e:
        print("DOWNLOAD FAILED", type(e).__name__, e); continue
    print("files:", [(Path(f).name, Path(f).stat().st_size) for f in files], "prices:", len(prices))
    for f in files[:1]:
        root = etree.parse(f, etree.XMLParser(recover=True, huge_tree=True)).getroot()
        print("root:", low(root), "children:", collections.Counter(low(c) for c in root).most_common(8))
        promos = [el for el in root.iter() if any(low(c) == "promotionid" for c in el)]
        print("promo elements:", len(promos), "tag:", collections.Counter(low(p) for p in promos))
        cnt = collections.defaultdict(collections.Counter)
        for p in promos:
            d = {low(c): (c.text or "").strip() for c in p if len(c) == 0}
            for k in ("rewardtype", "discounttype", "minqty", "maxqty", "isweightedpromo", "minnoofitemofered", "allowmultiplediscounts"):
                cnt[k][d.get(k)] += 1
            cnt["clubid"][tuple(sorted(c.text or "" for c in p.iter() if low(c) == "clubid"))] += 1
            cnt["nitems"][min(len([1 for c in p.iter() if low(c) == "itemcode"]), 50)] += 1
            cnt["hasMinPurchase"][bool(float(d.get("minpurchaseamnt") or 0))] += 1
            cnt["coupon"][tuple(c.text for c in p.iter() if low(c) == "additionaliscoupon")] += 1
        for k, v in cnt.items():
            print("  ", k, v.most_common(8))
        if promos:
            print("RAW FIRST:", etree.tostring(promos[0], encoding="unicode")[:1800].replace("\n", " "))
        for p in random.sample(promos, min(22, len(promos))):
            d = {low(c): (c.text or "").strip() for c in p if len(c) == 0}
            codes = [c.text for c in p.iter() if low(c) == "itemcode"][:2]
            clubs = [c.text for c in p.iter() if low(c) == "clubid"]
            keep = {k: d.get(k) for k in ("promotiondescription", "rewardtype", "discounttype", "minqty", "maxqty", "discountedprice", "discountedpricepermida", "discountrate", "minpurchaseamnt", "minnoofitemofered", "promotionstartdate", "promotionenddate", "isweightedpromo") if d.get(k) not in (None, "")}
            print(" >", keep, "clubs", clubs, "| items:", [(c, prices.get(c)) for c in codes])
