/* סל חכם – מנוע ההשוואה שרץ בדפדפן. תרגום נאמן של core.py (compare_data) ל-JavaScript.
   הנתונים (data/prices.json) נבנים כל יום ע"י GitHub Actions מקבצי המחירים הרשמיים של הרשתות. */
(function (global) {
  "use strict";

  const QUOTES = { "״": '"', "”": '"', "“": '"', "׳": "'", "’": "'", "‘": "'", "`": "'" };
  function norm(s) {
    s = String(s ?? "").replace(/[״”“׳’‘`]/g, c => QUOTES[c]).toLowerCase();
    s = s.replace(/[\-_/\\,;:()\[\]*+]/g, " ");
    return s.replace(/\s+/g, " ").trim();
  }
  const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const fmtG = x => String(Number(Number(x).toPrecision(6)));
  const isNum = c => c >= "0" && c <= "9";

  // ───────── רשימה וכמויות ─────────
  function vocab(items) {
    const v = new Map();
    for (const [key, spec] of Object.entries(items))
      for (const c of [key, ...(spec.aliases || [])]) v.set(norm(c), key);
    return v;
  }
  function parseList(text, items) {
    const v = vocab(items);
    const maxlen = Math.max(1, ...[...v.keys()].map(k => k.split(" ").length));
    const out = [];
    for (let chunk of String(text).split(/[,\n;،]+/)) {
      chunk = chunk.replace(/^\s*[-•*\d.)]+\s*/, "").trim();
      const words = norm(chunk).split(" ").filter(Boolean);
      let i = 0, buf = [];
      while (i < words.length) {
        let hit = null;
        for (let j = Math.min(words.length, i + maxlen); j > i; j--) {
          const ph = words.slice(i, j).join(" ");
          if (v.has(ph)) { hit = [j, ph]; break; }
          if (ph.startsWith("ו") && v.has(ph.slice(1))) { hit = [j, ph.slice(1)]; break; }
        }
        if (hit) { if (buf.length) { out.push(buf.join(" ")); buf = []; } out.push(hit[1]); i = hit[0]; }
        else { buf.push(words[i]); i++; }
      }
      if (buf.length) out.push(buf.join(" "));
    }
    return out;
  }

  const TERM_QTY = /(?:(\d+(?:[.,]\d+)?)\s*|(חצי|רבע)\s+)?(ק"ג|קג|קילו|קילוגרם|קילוגרמים|גרם|גר|ג'|ליטר|ליטרים|ל'|מ"ל|מל|יחידות|יחידה|יח'|יח)(?=\s|$)/;
  const STOP = new Set(["של", "את", "עם", "בערך", "כ", "גם"]);

  function parseTermQty(term) {
    const t = norm(term);
    const m = TERM_QTY.exec(t);
    if (!m) return [t, null, ""];
    const num = m[1] ? parseFloat(m[1].replace(",", ".")) : ({ "חצי": 0.5, "רבע": 0.25 }[m[2] || ""] ?? 1.0);
    const u = m[3];
    let target;
    if (["יחידות", "יחידה", "יח'", "יח"].includes(u)) target = [num, "u"];
    else if (["גרם", "גר", "ג'", 'מ"ל', "מל"].includes(u)) target = [num, "w"];
    else target = [num * 1000, "w"];
    let clean = (t.slice(0, m.index) + " " + t.slice(m.index + m[0].length)).trim().replace(/\s+/g, " ");
    return [clean || t, target, m[0].trim()];
  }

  function resolve(term, items) {
    const [t, qty, qtyTxt] = parseTermQty(term);
    const suffix = qtyTxt ? ` (${qtyTxt})` : "";
    const withQty = (key, spec) => {
      if (qty) {
        spec = { ...spec };
        const tgt = spec.target;
        if (!tgt || tgt[1] === qty[1] || qty[1] === "w") spec.target = [...qty];
      }
      return [key + suffix, spec, true];
    };
    for (const [key, spec] of Object.entries(items))
      if (t === norm(key) || (spec.aliases || []).some(a => norm(a) === t)) return withQty(key, spec);
    let best = null;
    for (const [key, spec] of Object.entries(items))
      for (const cand of [key, ...(spec.aliases || [])]) {
        const c = norm(cand);
        if (c && (new RegExp(`(^|\\s)${esc(c)}(\\s|$)`).test(t) || (t.length >= 3 && c.includes(t))))
          if (!best || c.length > best[0]) best = [c.length, key, spec];
      }
    if (best) return withQty(best[1], best[2]);
    const words = t.split(" ").filter(w => w && !STOP.has(w));
    return [String(term).trim(), { words, target: qty ? [...qty] : null, free: true }, false];
  }

  // ───────── התאמת מוצר ─────────
  function compileSpec(spec) {
    if (spec._c) return spec._c;
    const W = k => (spec[k] || []).map(norm).filter(Boolean);
    const c = {
      starts: W("starts"), any: W("any"), all: W("all"), none: W("none"), need: W("need"),
      words: (spec.words || []).map(w => new RegExp(`(^|[\\s.'"%])${esc(norm(w))}($|[\\s.'"%])`)),
      freeNone: spec.free ? FREE_NONE.filter(w => !(spec.words || []).join(" ").includes(w)) : [],
    };
    Object.defineProperty(spec, "_c", { value: c, enumerable: false });
    return c;
  }
  function matches(nname, spec) {
    const c = compileSpec(spec);
    if (c.starts.length && !c.starts.some(s => nname.startsWith(s))) return false;
    if (c.any.length && !c.any.some(s => nname.includes(s))) return false;
    if (c.all.length && !c.all.every(s => nname.includes(s))) return false;
    if (c.none.some(s => nname.includes(s))) return false;
    for (const re of c.words) if (!re.test(nname)) return false;
    return true;
  }
  const GLOBAL_NONE = ["קופון", "הנחה", "פיקדון", "פקדון", "מתנה", "לחתול", "לכלב", "לחתלתול", "פרמיו", "פריסקיז"].map(norm);
  const FREE_NONE = ["תערובת תיבול", "תיבול ל", "כריך", "בטעם", "טעם של", "מזון ל", "לחתול", "לכלב"].map(norm);

  function countInName(n) {
    // (?<!\d)(\d{1,2})\s*(יח|יחידות|ביצים|ב'|\*) | [smlx](\d{1,2})(?!\d) | (?<!\d)(\d{1,2})(?!\d)  – בלי lookbehind (תאימות ספארי)
    const re = /(^|\D)(\d{1,2})\s*(?:יח|יחידות|ביצים|ב'|\*)|[smlx](\d{1,2})(?!\d)|(^|\D)(\d{1,2})(?!\d)/;
    const m = re.exec(n);
    if (!m) return null;
    const v = parseInt(m[2] || m[3] || m[5], 10);
    return v >= 2 && v <= 60 ? v : null;
  }
  function label(target) {
    if (!target) return "";
    const [a, d] = target;
    if (d === "u") return `${fmtG(a)} יח'`;
    return a >= 1000 ? `${fmtG(a / 1000)} ק"ג/ליטר` : `${fmtG(a)} גרם`;
  }

  // מוצר במאגר: P[i] = {name, n (מנורמל), amount, dim, w (שקיל)}
  // cands: [product, shelfPrice, pid].  promos: Map(pid -> [{min, unit, club, desc}]) של הסניף.
  // מבצע "פשוט" (מיחידה אחת, לכל הלקוחות – או מועדון אם המשתמש סימן שיש לו) נכנס אוטומטית למחיר.
  // מבצע כמות ("3 ב-10") או מועדון לא נכנס למחיר, אלא מוחזר כהצעה (deal) אם הוא זול יותר ממה שנבחר.
  function bestFrom(cands, spec, promos, opt) {
    const target = spec.target;
    const minPrice = spec.min_price ?? 1.0;
    const c = compileSpec(spec);
    const useClub = !!(opt && opt.club);
    let best = null, deal = null;
    for (const [p, price, pid] of cands) {
      if (price < minPrice) continue;
      const nname = p.n;
      if (GLOBAL_NONE.some(w => nname.includes(w))) continue;
      if (c.freeNone.some(w => nname.includes(w))) continue;
      const weighted = !!p.w;
      if (c.need.length && !weighted && !c.need.some(w => nname.includes(w))) continue;
      const qd = p.dim ? [p.amount, p.dim] : null;
      let ratio = 1, size = "";
      if (target) {
        const [tAmount, tDim] = target;
        let amount = null;
        if (tDim === "u") {
          if (qd && qd[1] === "u" && qd[0] >= 2) amount = qd[0];
          else amount = tAmount > 1 ? countInName(nname) : 1;
        } else if (qd && qd[1] === "w") amount = qd[0];
        if (!amount) continue;
        ratio = amount / tAmount;
        if (!weighted && !(ratio >= 0.4 && ratio <= 2.5)) continue;
        if (weighted) size = 'שקיל, לק"ג';
        else if (tDim === "u") size = `${fmtG(amount)} יח'`;
        else size = amount >= 1000 ? `${fmtG(amount / 1000)} ק"ג/ליטר` : `${fmtG(amount)} גרם/מ"ל`;
      }
      let eff = price, applied = null;
      const ps = promos ? promos.get(pid) : null;
      if (ps) for (const d of ps) {
        const auto = d.min <= 1.001 && (!d.club || useClub);
        if (auto) { if (d.unit < eff) { eff = d.unit; applied = d; } }
        else {
          const dc = d.unit / ratio;
          if (!deal || dc < deal.cost) deal = { name: p.name, size, weighted, min: d.min, unit: d.unit, club: !!d.club, desc: d.desc, cost: dc };
        }
      }
      const cost = eff / ratio;
      if (!best || cost < best.cost)
        best = { name: p.name, price, eff, cost, shelfCost: price / ratio, size, weighted,
                 promo: applied ? { desc: applied.desc, club: !!applied.club } : null,
                 per: Math.abs(ratio - 1) > 0.001 ? label(target) : "" };
    }
    // הצעה מוצגת רק אם היא באמת זולה יותר (לפחות 3% ו-5 אגורות) ממה שנבחר
    if (best) best.deal = deal && deal.cost < best.cost - Math.max(0.05, 0.03 * best.cost) ? deal : null;
    return best;
  }

  function inferTarget(products, matchIds, spec) {
    const sizes = { w: [], u: [] };
    for (const [pid, price] of matchIds) {
      const p = products[pid];
      if (price < 1 || !p.dim) continue;
      sizes[p.dim].push(p.amount);
    }
    const dim = sizes.w.length >= sizes.u.length ? "w" : "u";
    const vals = sizes[dim].sort((a, b) => a - b);
    if (!vals.length) return null;
    const med = vals[Math.floor(vals.length / 2)];
    if (dim === "u") return [Math.max(1, Math.round(med)), "u"];
    return [med >= 100 ? Math.round(med / 10) * 10 : med, "w"];
  }

  function haversine(a, b) {
    const R = 6371, r = x => x * Math.PI / 180;
    const [la1, lo1, la2, lo2] = [r(a[0]), r(a[1]), r(b[0]), r(b[1])];
    const h = Math.sin((la2 - la1) / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin((lo2 - lo1) / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(h));
  }

  // ───────── טעינת המאגר ─────────
  function prepare(data) {
    const products = data.products.map(([name, amount, dim, w]) => ({ name, n: norm(name), amount, dim, w }));
    const stores = data.stores.map(s => {
      const prices = new Map();
      const it = s.items;
      for (let i = 0; i < it.length; i += 2) {
        const pid = it[i], price = it[i + 1];
        // אותו מוצר בשני ברקודים באותו סניף – שומרים את הזול (כמו גרסת המחשב שבוחרת את הזול מכל השורות)
        if (!prices.has(pid) || price < prices.get(pid)) prices.set(pid, price);
      }
      const promos = new Map();
      const pr = s.promos || [], pd = s.pd || [];
      for (let i = 0; i + 4 < pr.length; i += 5) {
        if (pr[i] < 0) continue;
        let a = promos.get(pr[i]); if (!a) promos.set(pr[i], a = []);
        a.push({ min: pr[i + 1], unit: pr[i + 2], club: pr[i + 3], desc: pd[pr[i + 4]] || "" });
      }
      return { ...s, prices, promos };
    });
    // אינדקס הפוך: לכל מוצר – באילו סניפים ובאיזה מחיר
    const where = new Map();
    stores.forEach((s, si) => s.prices.forEach((price, pid) => {
      let a = where.get(pid); if (!a) where.set(pid, a = []); a.push(si, price);
    }));
    return { ...data, products, stores, where };
  }

  // ───────── השוואת סל ─────────
  function compare(db, terms, opt = {}) {
    const cfg = db.config, items = db.items;
    const sort = opt.sort === "distance" ? "distance" : "price";
    const maxKm = opt.max_km ? Number(opt.max_km) : null;
    terms = (terms || []).map(x => String(x).trim()).filter(Boolean);
    if (!terms.length) return { error: "לא זיהיתי מוצרים. כתוב רשימה מופרדת בפסיקים או בשורות." };
    const resolved = [], seen = new Set();
    for (const t of terms) { const r = resolve(t, items); if (!seen.has(r[0])) { seen.add(r[0]); resolved.push(r); } }

    const home = opt.lat && opt.lon ? [opt.lat, opt.lon] : [cfg.home_lat, cfg.home_lon];
    const results = []; let unknownDist = 0;
    const exclude = (cfg.exclude_store_words || []).map(norm);
    db.stores.forEach((s, si) => {
      if (!cfg.chains.includes(s.chain) || exclude.some(x => norm(s.name).includes(x))) return;
      const d = s.lat ? haversine(home, [s.lat, s.lon]) : null;
      const limit = maxKm || cfg.radius_km;
      if (d !== null && d > limit) return;
      if (maxKm && d === null) { unknownDist++; return; }
      results.push({ si, chain: s.chain, title: s.name, address: s.address, dist: d, picks: {} });
    });
    if (!results.length)
      return { error: maxKm ? `אין סניפים במרחק של עד ${fmtG(maxKm)} ק"מ ממך. נסה להגדיל את המרחק.` : "לא נמצאו סניפים ברדיוס שהוגדר." };

    for (let i = 0; i < resolved.length; i++) {
      let [disp, spec, known] = resolved[i];
      // מועמדים: מוצרים שמתאימים לתיאור, ובאילו סניפים הם נמכרים
      const perStore = new Map(), flat = [];
      db.products.forEach((p, pid) => {
        if (!matches(p.n, spec)) return;
        const w = db.where.get(pid); if (!w) return;
        for (let k = 0; k < w.length; k += 2) {
          let a = perStore.get(w[k]); if (!a) perStore.set(w[k], a = []);
          a.push([p, w[k + 1], pid]); flat.push([pid, w[k + 1]]);
        }
      });
      if (spec.free && !spec.target) { spec = { ...spec, target: inferTarget(db.products, flat, spec) }; resolved[i] = [disp, spec, known]; }
      for (const r of results) r.picks[disp] = bestFrom(perStore.get(r.si) || [], spec, db.stores[r.si].promos, opt);
    }

    const cheapest = {};
    for (const [disp] of resolved) {
      let m = null;
      for (const r of results) { const p = r.picks[disp]; if (p && (!m || p.cost < m[0])) m = [p.cost, r]; }
      cheapest[disp] = m;
    }
    const found = r => Object.values(r.picks).filter(Boolean).length;
    const total = r => Object.values(r.picks).reduce((a, p) => a + (p ? p.cost : 0), 0);
    const effective = r => total(r) + Object.entries(r.picks).reduce((a, [d, p]) => a + (!p && cheapest[d] ? cheapest[d][0] : 0), 0);
    const missingN = r => Object.entries(r.picks).filter(([d, p]) => !p && cheapest[d]).length;
    const anywhere = Object.values(cheapest).filter(Boolean).length || 1;
    const penalty = cfg.missing_item_penalty ?? 5;
    const minFound = Math.max(1, Math.ceil(anywhere * (cfg.min_coverage ?? 0.6)));
    for (const r of results) { r.found = found(r); r.eff = effective(r); r.key = r.eff + penalty * missingN(r); }
    results.sort((a, b) => ((a.found < minFound) - (b.found < minFound)) || (a.key - b.key));

    const branches = {};
    for (const r of results) branches[r.chain] = (branches[r.chain] || 0) + 1;
    let perChain = [];
    const seenChain = new Set();
    const pool = sort === "distance"
      ? results.filter(r => r.found >= minFound).sort((a, b) =>
          ((a.dist === null) - (b.dist === null)) || ((a.dist ?? 0) - (b.dist ?? 0)) || (a.eff - b.eff))
      : results.filter(r => r.found >= minFound);
    for (const r of pool) if (!seenChain.has(r.chain)) { seenChain.add(r.chain); perChain.push(r); }
    if (!perChain.length) perChain = results.slice(0, 5);

    const r2 = x => Math.round(x * 100) / 100;
    const pickJson = (disp, p, known) => !p ? { item: disp, found: false, free_search: !known } : {
      item: disp, found: true, free_search: !known, product: p.name, size: p.size,
      shelf_price: r2(p.price), cost: r2(p.cost), per: p.per || "",
      promo: p.promo ? { desc: p.promo.desc, club: p.promo.club, shelf_cost: r2(p.shelfCost), price: r2(p.eff) } : null,
      deal: p.deal ? { product: p.deal.name, same: p.deal.name === p.name, size: p.deal.size, weighted: p.deal.weighted,
                       min: p.deal.min, total: r2(p.deal.unit * p.deal.min), unit: r2(p.deal.unit), club: p.deal.club,
                       desc: p.deal.desc, cost: r2(p.deal.cost), saving: r2(p.cost - p.deal.cost) } : null };
    const picksOf = r => Object.values(r.picks).filter(Boolean);
    const chainName = c => (db.chain_names || {})[c] || c;
    const storeJson = r => ({
      chain: r.chain, chain_name: chainName(r.chain), store: r.title, address: r.address,
      distance_km: r.dist !== null ? Math.round(r.dist * 10) / 10 : null,
      total: r2(r.eff), basket_here: r2(total(r)), branches: branches[r.chain] || 1,
      promo_saving: r2(picksOf(r).reduce((a, p) => a + (p.promo ? p.shelfCost - p.cost : 0), 0)),
      deals: picksOf(r).filter(p => p.deal).length,
      deal_saving: r2(picksOf(r).reduce((a, p) => a + (p.deal ? p.cost - p.deal.cost : 0), 0)),
      stale: db.stores[r.si].stale || null,
      found: resolved.filter(([d]) => r.picks[d]).map(([d]) => d),
      missing: resolved.filter(([d]) => !r.picks[d] && cheapest[d]).map(([d]) => d),
      picks: resolved.map(([d, , k]) => pickJson(d, r.picks[d], k)),
    });
    const split = resolved.map(([d, , k]) => {
      const v = cheapest[d];
      if (!v) return { item: d, found: false, free_search: !k };
      const r = v[1];
      return { ...pickJson(d, r.picks[d], k), chain: r.chain, chain_name: chainName(r.chain), store: r.title };
    });
    const best = results[0];
    const splitTotal = r2(Object.values(cheapest).filter(Boolean).reduce((a, v) => a + v[0], 0));
    return {
      items: resolved.map(([d]) => d),
      not_found_anywhere: resolved.filter(([d]) => !cheapest[d]).map(([d]) => d),
      stores_checked: results.length, last_update: db.last_update, approx_location: !(opt.lat && opt.lon),
      sort, max_km: maxKm, unknown_distance_excluded: unknownDist, club: !!opt.club,
      has_promos: db.stores.some(s => s.promos && s.promos.size > 0),
      cheapest_total: r2(Math.min(...perChain.map(r => r.eff))),
      ranking: perChain.map(storeJson),
      split: { total: splitTotal, saving: r2(Math.max(best.eff - splitTotal, 0)), items: split },
    };
  }

  global.Basket = { norm, parseList, resolve, compare, prepare, haversine };
  if (typeof module !== "undefined") module.exports = global.Basket;
})(typeof window !== "undefined" ? window : globalThis);
