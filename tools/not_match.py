#!/usr/bin/env python3
"""
Match product names in the Complaints sheet to NPRA notification numbers (NOT...), from Malaysia's open data.

Why: a complaint is "Ready to file" only when it carries a notification number, and the scraper never invents one
(QUEST3+ has no API here, and a wrong NOT number in a complaint is worse than a blank one). data.gov.my publishes the
list of notified cosmetic products (Produk Kosmetik Bernotifikasi), so a product name that matches one entry with
confidence can be given its number, flagged for a person to confirm on QUEST3+ before filing.

    https://storage.data.gov.my/healthcare/cosmetic_notifications.parquet   (notif_no, product, company, date_notif)

This file only DECIDES. It reads two local files and writes a plan; it never touches the sheet:

    python3 tools/not_match.py --parquet cn.parquet --rows rows.json --out plan.json

rows.json is {"<sheet row>": {"id", "date", "brand", "plat", "L", "M", "k"}} for the open Complaints rows that have a
product name (L) and no notification number yet. The plan lists, per row, the match and its tier:

    exact    the product name equals an entry (after dropping shade codes in brackets)
    strong   a brand account: at least 80% of the words are shared, the brand is in the entry's product or company name,
             at least two words besides the brand match and no form word (gel, cream, serum...) differs. Any other
             account (a doctor, a shop): exact names only, three words or more
    hold     a match exists but must not be written: see `why`

What is never matched: a name that is only the brand, or fewer than two words besides the brand; a name that is a
category, a range or several products ("no specific product", "range", ";"); two or more
different products or companies tied at the best tier; a notification whose month (YYMM inside the NOT number) is
later than the post's month, since the product was not notified yet when the post went up.

`date_notif` in the open data is the latest RENEWAL date, not the first notification, so the first notification is
read from the YYMM inside the number.
"""
import argparse, json, re, sys, unicodedata
from collections import defaultdict

STOP = {"the", "by", "new", "my", "malaysia", "in", "with", "and", "for", "of", "a", "an", "is", "she", "shade",
        "colour", "color", "official", "series", "range", "set", "pack", "suncare", "skincare", "from", "to"}
NOT_RE = re.compile(r"^NOT(\d{2})(\d{2})", re.I)
# a form or type word: "Spotless Lotion" is not "Spotless Serum", whatever else they share
VARIANT = {"gel", "cream", "serum", "lotion", "cleanser", "mask", "foam", "oil", "balm", "shampoo", "conditioner",
           "spray", "wash", "toner", "sunscreen", "moisturiser", "moisturizer", "mist", "essence", "ampoule", "powder",
           "stick", "roll", "deodorant", "wipes", "patch", "scrub", "mousse", "milk", "elixir", "soap", "toothpaste",
           "lipstick", "mascara", "foundation", "cushion", "tint", "blush", "palette", "perfume", "parfum", "booster"}
# a name that says it is not a product, or names several
HEDGE = re.compile(r"no specific|not specific|specific product|category|brand line|\brange\b|collection|not legib|unclear|"
                   r"logo only|unidentified|several|and other|\bsets?\b|;| / |\bor\b", re.I)


def norm(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\([^)]*\)", " ", s)              # shade codes and batch numbers in brackets
    s = s.replace("&", " and ").replace("+", " plus ")
    s = re.sub(r"[^a-z0-9% ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s, brand_tokens=frozenset()):
    return [t for t in norm(s).split() if t not in STOP and t not in brand_tokens]


def not_month(num):
    m = NOT_RE.match(num or "")
    return (2000 + int(m.group(1)), int(m.group(2))) if m else None


def build_index(products):
    """products: [(notif_no, product, company)] -> (list of entries, token -> entry ids)"""
    entries, idx = [], defaultdict(set)
    for n, p, c in products:
        toks = tokens(p)
        if not toks:
            continue
        i = len(entries)
        entries.append({"no": n, "product": p, "company": c, "norm": " ".join(norm(p).split()), "toks": set(toks)})
        for t in set(toks):
            idx[t].add(i)
    return entries, idx


def brand_tokens_of(brand, plat):
    # an account handle (a doctor, a shop) is not a brand; only a named brand can be taken out of the product name
    if "_" in brand or "." in brand or brand.islower():
        return frozenset()
    return frozenset(t for t in norm(brand).split() if t not in STOP)


def match_one(row, entries, idx):
    L = row.get("L", "")
    brand = row.get("brand", "")
    bt = brand_tokens_of(brand, row.get("plat", ""))
    toks = tokens(L, bt)
    want = set(toks)
    if len(want) < 2:
        return {"tier": None, "why": "name too short or brand only"}
    if HEDGE.search(L):
        return {"tier": None, "why": "name is a category, a range or several products"}
    nL = " ".join(norm(L).split())
    # candidates: entries sharing one of the two rarest words
    rare = sorted(want, key=lambda t: len(idx.get(t, ())))
    pool = set()
    for t in rare[:2]:
        pool |= idx.get(t, set())
    scored = []
    for i in pool:
        e = entries[i]
        et = {t for t in e["toks"] if t not in bt}
        if bt:
            # a named brand must be in the entry's product name or its company: "Moisture Restore Cream" from Dr Dermis
            # is not another brand's cream of the same name
            text = e["norm"] + " " + norm(e["company"])
            if not all(t in text.split() for t in bt):
                continue
        else:
            # a doctor's or shop's account names other brands' products: the entry's first word (its brand) must be in the name
            first = next(iter(tokens(e["product"])), None)
            if first not in want:
                continue
        inter = len(want & et)
        union = len(want | et)
        if inter < 2 or (not bt and inter < 3):
            continue
        jac = inter / union
        if e["norm"] == nL:
            tier = "exact"
        elif (want ^ et) & VARIANT:
            continue                                   # one says lotion, the other serum
        elif bt and jac >= 0.8:
            tier = "strong"
        else:                                           # an account that is not a brand: exact names only
            continue
        scored.append((tier, i))
    if not scored:
        return {"tier": None, "why": "no confident entry in the open data"}
    order = {"exact": 0, "strong": 1}
    best = min(order[s[0]] for s in scored)
    tier = ["exact", "strong"][best]
    top_entries = sorted((entries[i] for t, i in scored if order[t] == best), key=lambda e: e["no"])
    out = {"tier": tier, "options": [{"no": e["no"], "product": e["product"], "company": e["company"]} for e in top_entries[:6]]}
    post = None
    m = re.match(r"(\d{4})-(\d{2})", row.get("date", "") or "")
    if m:
        post = (int(m.group(1)), int(m.group(2)))
    valid = [e for e in top_entries if not post or (not_month(e["no"]) or (0, 0)) <= post]
    if not valid:
        nm = not_month(top_entries[0]["no"])
        out.update(tier="hold", why="notified after the post (first notification %s)" % ("%d-%02d" % nm if nm else "?"))
        return out
    top_entries = valid
    names = {(e["norm"], e["company"].lower()) for e in top_entries}
    if len({n for n, _ in names}) > 1 or len({c for _, c in names}) > 1:
        out.update(tier="hold", why="tie: %d products / %d companies at the best tier" % (
            len({n for n, _ in names}), len({c for _, c in names})))
        return out
    if len(top_entries) > 1:
        # one product and company under several numbers (shades, renewals): not safe to choose one
        out.update(tier="hold", why="several numbers for one product: %s" % ", ".join(e["no"] for e in top_entries[:5]))
        return out
    e = top_entries[0]
    out.update(no=e["no"], product=e["product"], company=e["company"], diff=sorted(want ^ (e["toks"] - bt))[:6])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    import pyarrow.parquet as pq
    t = pq.read_table(a.parquet)
    products = list(zip(t["notif_no"].to_pylist(), t["product"].to_pylist(), t["company"].to_pylist()))
    entries, idx = build_index(products)
    rows = json.load(open(a.rows))
    plan = {}
    for r, row in rows.items():
        plan[r] = match_one(row, entries, idx)
        plan[r]["id"] = row.get("id")
        plan[r]["L"] = row.get("L")
    json.dump(plan, open(a.out, "w"), ensure_ascii=False, indent=1)
    from collections import Counter
    print(Counter(p.get("tier") or "none" for p in plan.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
