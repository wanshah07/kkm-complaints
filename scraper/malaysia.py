"""
Is this post probably Malaysian? (Wan, 24 Nov 2026: "add Malaysia filter", after the first hashtag run filed 46 Skintific
posts and a good half were Indonesian, Thai and Filipino shops and consumers, which KKM cannot act on.)

A hashtag page is global, so a post has to show where it comes from. Instagram gives no country, so this reads the caption:

    my       a Malaysian signal outweighs any other country's: an #skintificmy tag, "@skintific.my", "Malaysia", a state
             or town, an "RM 59" price, or words only Malay-as-written-in-Malaysia uses (korang, jom, nak, apabila, anjal)
    not_my   another country's signal wins: Thai or Hangul script, Indonesian words (banget, kulitmu, pake, Rp129.000,
             ongkir), Filipino tags and place names (#skintificph, Manila), Singapore or Brunei markers
    unclear  nothing either way, usually an English caption with no place in it

Indonesian and Malaysian Malay share most of their vocabulary (yang, dan, untuk, kulit), so only words that differ count.
Two modes in main.py: `strict` keeps only `my`; `loose` also keeps `unclear` and drops only `not_my`. An account that is
already a Targets row is kept whatever its caption says: Wan chose those accounts.

This is a heuristic and it is meant to be argued with: the verdict and the words that decided it are printed for every post
the filter drops, so a wrong drop is visible and the word lists can be corrected.
"""
from __future__ import annotations

import re
from typing import List, Tuple

# Malay as written in Malaysia (Indonesian uses mau, nggak, udah, banget instead)
MY_WORDS = {"korang", "jom", "tak", "kat", "je", "mcm", "xde", "xnak", "nak", "tau", "tgk", "apabila", "selepas",
            "nampak", "anjal", "berseri", "bermaya", "seawal", "rahsia", "kedai", "mak", "lah", "kan", "betul", "teruk"}
# Indonesian words that Malaysian Malay does not use
ID_WORDS = {"banget", "bgt", "nggak", "gak", "enggak", "kamu", "kulitmu", "kulitku", "wajahmu", "aku", "kalian", "udah",
            "yuk", "yukk", "nih", "deh", "dong", "kok", "sih", "pake", "bener", "bener2", "kenalan", "saatnya",
            "sekaligus", "terlihat", "tampil", "diskon", "ongkir", "gratis", "bundling", "rp", "mau", "bunda", "sultan",
            "cuma", "tenang", "dapet", "gemoy", "kebutuhan", "rahasia", "toko", "masker", "lembar", "berupa",
            "berfungsi", "bingung", "menyelamatkan", "ternyata", "bermasalah", "kusam", "jerawatku", "jerawatnya", "seperti",
            "habis", "senin", "sabtu", "jumat", "selasa", "rabu", "kamis", "minggu", "inbox", "pemesanan", "pesan",
            "bestie", "kuy", "ayo", "pengen", "banyak2", "murah", "terbaru", "sekali", "lagi", "emang", "pas",
            "bisa", "meresap", "detik", "digunakan", "dilengkapi", "menggabungkan", "membantu", "melindungi", "menjadikan",
            "dapat", "bebas", "mengontrol", "produksi", "menggunakan"}
# only count the ID words that are not also common Malaysian ones
ID_WORDS -= {"terbaru", "sekali", "lagi", "banyak2", "murah", "pas", "habis", "pesan", "inbox", "ternyata", "kusam",
             "seperti", "mau", "bestie", "emang", "cuma", "dapat", "bebas", "produksi", "membantu", "melindungi",
             "menjadikan", "menggunakan", "digunakan", "mengontrol", "dilengkapi", "menggabungkan"}  # also Malaysian
ID_PLACES = {"indonesia", "jakarta", "surabaya", "bandung", "semarang", "jogja", "yogyakarta", "bali", "medan",
             "makassar", "jatim", "jateng", "jabar", "maospati", "tangerang", "bekasi", "depok", "bogor", "malang"}
FIL_WORDS = {"mga", "naman", "talaga", "kasi", "yung", "sana", "pesos", "pilipinas", "philippines", "manila", "cebu",
             "davao", "luzon", "quezon", "makati"}
OTHER_PLACES = {"singapore", "brunei", "bangkok", "thailand", "vietnam", "hanoi", "saigon", "cambodia", "myanmar"}
MY_PLACES = {"malaysia", "kuala", "lumpur", "selangor", "johor", "penang", "sabah", "sarawak", "perak", "kedah",
             "kelantan", "terengganu", "pahang", "melaka", "putrajaya", "cyberjaya", "klang", "subang", "bangsar",
             "ipoh", "kuching", "shah", "alam", "petaling", "cheras", "ampang", "seremban", "kota", "kinabalu"}
MY_PLACES -= {"kota", "alam", "shah", "kuala"}      # too common alone; "kuala lumpur" is matched by "lumpur"
# hashtags / handles that name a country
COUNTRY_TAGS_NOT_MY = ("skintificid", "skintificph", "skintificthai", "skintificth", "skintificsg", "skintificvn",
                       "skintificindonesia", "skintificphilippines", "skintificsingapore",
                       "skintific.id", "skintific.ph", "skintific.th", "skintific.sg", "skintific.vn")
COUNTRY_TAGS_MY = ("skintificmy", "skintific.my", "skintificmalaysia")

_THAI = re.compile(r"[฀-๿]")
_HANGUL_KANA = re.compile(r"[가-힯぀-ヿ]")
_RM_PRICE = re.compile(r"\brm\s?\d", re.I)
_RP_PRICE = re.compile(r"\brp\s?\.?\s?\d", re.I)
_WORD = re.compile(r"[a-z0-9']+")


def classify(text: str) -> Tuple[str, str]:
    """('my' | 'not_my' | 'unclear', the evidence that decided it)."""
    t = (text or "").lower()
    if not t.strip():
        return "unclear", "no caption"
    my: List[str] = []
    other: List[str] = []
    flat = re.sub(r"[#@]", " ", t)
    words = _WORD.findall(flat)
    wset = set(words)
    for w in sorted(wset & MY_WORDS):
        my.append(w)
    for w in sorted(wset & MY_PLACES):
        my.append(w)
    for tag in COUNTRY_TAGS_MY:
        if tag in t:
            my += [tag, tag]            # an explicit country tag counts double
    if _RM_PRICE.search(t):
        my += ["RM price", "RM price"]
    for w in sorted(wset & ID_WORDS):
        other.append(w)
    for w in sorted(wset & (ID_PLACES | OTHER_PLACES | FIL_WORDS)):
        other += [w, w]
    for tag in COUNTRY_TAGS_NOT_MY:
        if tag in t:
            other += [tag, tag]
    if _RP_PRICE.search(t):
        other += ["Rp price", "Rp price"]
    if _THAI.search(text or "") or _HANGUL_KANA.search(text or ""):
        other += ["Thai/Korean/Japanese script"] * 4
    if len(other) > len(my):
        return "not_my", ", ".join(dict.fromkeys(other))[:120]
    if len(my) > len(other):
        return "my", ", ".join(dict.fromkeys(my))[:120]
    if my:
        return "unclear", f"mixed: {', '.join(dict.fromkeys(my))[:50]} vs {', '.join(dict.fromkeys(other))[:50]}"
    return "unclear", "no country signal"


def keep(verdict: str, mode: str) -> bool:
    """strict keeps only 'my'; loose keeps 'my' and 'unclear'; anything else keeps everything."""
    if mode == "strict":
        return verdict == "my"
    if mode == "loose":
        return verdict in ("my", "unclear")
    return True
