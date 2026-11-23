"""
Sheet side of the NOT-number routine, for the Composio workbench (not run by GitHub).

The sheet is written through Composio (account googlesheets_serau-tucker); the repository has no key for it. This file
is fetched into a workbench cell and exec'd, so the logic lives here and not in a prompt:

    import requests
    exec(requests.get("https://raw.githubusercontent.com/wanshah07/kkm-complaints/main/tools/not_workbench.py").text)

It defines two things and runs nothing by itself.

    export_candidates()        -> {row: {id, date, brand, plat, L, M, k}}  open rows with a product name and no
                                  notification number yet (own-brand rows left out); saved to /mnt/files/not_rows.json
    start_writer(plan, today)  -> state dict; a background thread writes each planned row and records the outcome

`plan` is a list of [row, id, L, notif, product, company, "e"|"s", diff] from tools/not_match.py (exact and strong
only; weak matches are never written). Before any write the row is read again and must still be the same ID, open
(New / In-Progress), without a notification number, with the same product name and an Iklan Kosmetik (or empty)
route. Written per row: Nombor Notifikasi (M), a Remarks note (K) saying where the number came from and to confirm it
on QUEST3+ before filing, the `[SAHKAN: semak QUEST3+]` marker in Reason / Remarks / Deskripsi replaced by the number
(I, K, O), and Updated At (U). Never Status (J), never a dismissal, never a filing.

A row whose number is already in M but whose Remarks note is missing (a write that died half way) is finished, not
skipped. The sheet's read quota is 60 a minute: the writer reads 12 rows at a time and retries a failed write.
"""
import datetime, json, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor

SID = "10ZgXAzG3b3cTp_0vHAymhBZEb_LzwYH3BXNvP8ubyhs"
ACC = "googlesheets_serau-tucker"
# the own-brand list of apps-script/Code.gs (Skintific came off it on 2 Nov 2026) plus Valorith, which is Wan's too
OWN = ["facerinna", "facerina", "valorith", "lumixpert", "bodibreze", "nextprime", "body breze", "bodybreze",
       "next prime"]
NOT_NUM = re.compile(r"^NOT\s*\d", re.I)
QM = re.compile(r"\[(?:SAHKAN|VERIFY)[^\]]*(?:QUEST3|\bNOT\b|notifikasi)[^\]]*\]", re.I)


def _get(ranges):
    res, err = run_composio_tool("GOOGLESHEETS_BATCH_GET", {"spreadsheet_id": SID, "ranges": ranges}, account=ACC)
    if err:
        raise RuntimeError(str(err)[:200])
    return res["data"]["valueRanges"]


def export_candidates(path="/mnt/files/not_rows.json"):
    last = 1 + len(_get(["Complaints!A2:A"])[0].get("values") or [])      # an open range stops at the last row
    cols = ["A", "B", "C", "D", "J", "K", "L", "M", "N"]
    rows = {}
    for s in range(2, last + 1, 500):
        e = min(s + 499, last)
        vr = _get([f"Complaints!{c}{s}:{c}{e}" for c in cols])
        data = dict(zip(cols, [v.get("values") or [] for v in vr]))
        g = lambda c, i: (data[c][i][0] if i < len(data[c]) and data[c][i] else "")
        for i in range(e - s + 1):
            st = g("J", i) or "New"
            if st not in ("New", "In-Progress"):
                continue
            blob = (g("C", i) + g("K", i) + g("L", i)).lower()
            if any(o in blob for o in OWN) or g("N", i) not in ("", "Iklan Kosmetik"):
                continue
            if not g("L", i).strip() or NOT_NUM.match(g("M", i) or ""):
                continue
            rows[s + i] = {"id": g("A", i), "date": g("B", i), "brand": g("C", i), "plat": g("D", i),
                           "L": g("L", i), "M": g("M", i), "k": g("K", i)[:400]}
    json.dump(rows, open(path, "w"))
    return rows


def repl(text, notif):
    def f(m):
        pre = text[max(0, m.start() - 45):m.start()]
        if re.search(r"notifikasi[^.\n]{0,25}:\s*$", pre, re.I):
            return notif + " (dipadankan daripada data.gov.my; sahkan di QUEST3+)"
        return "Nombor notifikasi " + notif + " (dipadankan daripada data.gov.my; sahkan di QUEST3+)"
    return QM.sub(f, text)


def start_writer(plan, today, done_path="/mnt/files/nw_done.json"):
    st = {"done": set(), "log": [], "err": [], "running": True}

    def put(cell, val):
        if val[:1] in "=+-@":
            val = "'" + val
        for attempt in range(4):
            r2, e2 = run_composio_tool("GOOGLESHEETS_BATCH_UPDATE", {
                "spreadsheet_id": SID, "sheet_name": "Complaints", "first_cell_location": cell,
                "values": [[val]], "value_input_option": "RAW"}, account=ACC)
            if not e2:
                return
            time.sleep(4 + 6 * attempt)
        raise RuntimeError(f"{cell}: {str(e2)[:100]}")

    def worker():
        try:
            while True:
                todo = [w for w in plan if w[0] not in st["done"]][:12]
                if not todo:
                    break
                rngs = []
                for w in todo:
                    rngs += [f"Complaints!A{w[0]}", f"Complaints!I{w[0]}:O{w[0]}"]
                try:
                    vr = _get(rngs)
                except Exception as e:
                    st["err"].append(str(e)[:120]); time.sleep(10); continue
                cur = {}
                for i, w in enumerate(todo):
                    a = vr[2 * i].get("values") or [[""]]
                    b = (vr[2 * i + 1].get("values") or [[]])[0]
                    cur[w[0]] = ((a[0][0] if a and a[0] else ""), b + [""] * (7 - len(b)))
                now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

                def do(w):
                    r, rid, L, notif, prod, comp, cls, diff = w
                    try:
                        a, (I, J, K, L2, M, N, O) = cur[r]
                        has_note = "NOT dipadankan" in K
                        left = bool(QM.search(I) or QM.search(K) or QM.search(O))
                        # a number already in M is finished, not skipped, when the note or a marker replacement is missing
                        resumed = (M or "").strip() == notif and (not has_note or left)
                        if a != rid or J not in ("New", "In-Progress") or L2 != L or N not in ("", "Iklan Kosmetik") \
                                or ((M or "").strip() and not resumed):
                            st["log"].append((r, "skip", a == rid, J, (M or "")[:12], L2 == L, N)); st["done"].add(r); return
                        note = (f" | NOT dipadankan {today} daripada data.gov.my (Produk Kosmetik Bernotifikasi): {prod} ({comp})")
                        if cls == "s":
                            note += f"; padanan hampir (nama dalam aduan: '{L}', beza: {diff or 'susunan perkataan'})"
                        note += "; sahkan di QUEST3+ sebelum failkan"
                        K2, O2, I2 = repl(K, notif) + ("" if has_note else note), repl(O, notif), repl(I, notif)
                        if not resumed:
                            put(f"M{r}", notif)
                        if K2 != K:
                            put(f"K{r}", K2)
                        if O2 != O:
                            put(f"O{r}", O2)
                        if I2 != I:
                            put(f"I{r}", I2)
                        put(f"U{r}", now)
                        st["done"].add(r); st["log"].append((r, "resumed" if resumed else "ok"))
                    except Exception as ex_:
                        st["err"].append((r, str(ex_)[:120]))

                with ThreadPoolExecutor(3) as ex:
                    list(ex.map(do, todo))
                json.dump(sorted(st["done"]), open(done_path, "w"))
        finally:
            st["running"] = False

    threading.Thread(target=worker, daemon=True).start()
    return st
