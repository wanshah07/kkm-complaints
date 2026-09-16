"""
Deterministic pre-screen for NPRA Annex I Part 8 (Guideline for Cosmetic Claims) and
Part 10 (Guideline for Cosmetic Advertisement), Guidelines for Control of Cosmetic
Products in Malaysia, 2nd ed. (Aug 2022).

Sources
  Part 8 : https://www.npra.gov.my/images/Guidelines_Central/Guidelines_on_Cosmetic/Annex_I_part_8-GUIDELINE_FOR_COSMETIC_CLAIMS.pdf
  Part 10: https://www.npra.gov.my/images/Guidelines_Central/Guidelines_on_Cosmetic/Annex_I_part_10-_Guideline_for_Cosmetic_Advertisement.pdf

This module never decides alone. It (a) short-circuits obviously clean posts so the LLM
is not paid for them, (b) hands the LLM a list of candidate hits with the citation to
check, and (c) serves as the offline fallback when no LLM key is configured.

Every pattern carries: category (the sheet's "Violation Type"), the citation, a short
reason, and the severity the pattern alone justifies ("unacceptable" or "risky").
Patterns cover English and Bahasa Malaysia (never Bahasa Indonesia forms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

PART8 = "Annex I Part 8 (Guideline for Cosmetic Claims)"
PART10 = "Annex I Part 10 (Guideline for Cosmetic Advertisement)"

CATEGORIES = {
    "medicinal": "Medicinal / disease claim",
    "mechanism": "Mechanism claim (collagen, melanin, DNA, cells)",
    "endorsement": "Professional endorsement (doctor / dermatologist)",
    "sunscreen": "Prohibited sunscreen wording",
    "safety": "Safety / no-side-effect claim",
    "absolute": "Absolute / permanent result",
    "comparison": "Comparison or disparagement",
    "before_after": "Before-after without time elapsed",
    "gmp_moh": "GMP / MOH / approval reference",
    "prohibited_ref": "Prohibited ingredient or procedure reference",
    "quantitative": "Unsubstantiated quantitative claim",
    "other": "Other",
}


@dataclass
class Rule:
    key: str
    category: str
    pattern: str
    reason: str
    citation: str
    severity: str = "unacceptable"  # unacceptable | risky
    flags: int = re.IGNORECASE
    _rx: re.Pattern = field(init=False, repr=False)

    def __post_init__(self):
        self._rx = re.compile(self.pattern, self.flags)


@dataclass
class Hit:
    rule_key: str
    category: str
    matched: str
    context: str
    reason: str
    citation: str
    severity: str


# --- Pattern bank -------------------------------------------------------------
# Word boundaries are loose on purpose: social captions carry hashtags, emoji and
# BM affixes (merawat / dirawat / rawatan). Each regex is a candidate, not a verdict.
RULES: List[Rule] = [
    # Medicinal / disease drift — Part 8 skin table + 5-step process gate 4
    Rule("treat_cure_heal", "medicinal",
         r"\b(treat(?:s|ed|ing|ment)?|cure[sd]?|curing|heal(?:s|ed|ing)?|me?rawat|dirawat|rawatan|(?:me)?nyembuh(?:kan)?|sembuh(?:kan)?|penawar|ubat(?:i|kan)?)\b"
         r"(?![\s\w]{0,20}\b(barrier|lapisan pelindung)\b)",
         "Treat / cure / heal wording presents the product as treating a condition.",
         f"{PART8}, Skin products: 'Heals, treats or stops acne' and treatment of skin conditions are unacceptable; swap to prevent / control / reduce."),
    Rule("named_disease", "medicinal",
         r"\b(eczema|ekzema|psoriasis|dermatitis|atopic|atopik|rosacea|melasma|vitiligo|acne\s+vulgaris|fungal|kulat|infection|infeksi|jangkitan|inflammat(?:ion|ory)|anti-?inflammatory|radang|keradangan|wound|luka|burns?|melecur|bruise|lebam|varicose|prickly\s+heat|nappy\s+rash|ruam|rash|warts?|ketuat|skin\s+cancer|kanser)\b",
         "Names a skin disease or medical condition.",
         f"{PART8}, Skin products: reference to skin diseases (eczema, psoriasis, atopic dermatitis, rosacea, rash, wounds, burns) is unacceptable."),
    Rule("acne_treat", "medicinal",
         r"\b(stop(?:s)?|heal(?:s)?|treat(?:s)?|eliminat(?:e|es)|remove(?:s)?|hilang(?:kan)?|hapus(?:kan)?|rawat)\b[\s\w]{0,15}\b(acne|jerawat|pimples?|breakouts?)\b",
         "Claims to heal / stop / remove acne rather than control or reduce it.",
         f"{PART8}, Skin products: 'Heals, treats or stops acne' unacceptable; 'Prevent / control / reduce acne' acceptable."),
    Rule("scar_removal", "medicinal",
         r"\b(remov(?:e|es|al)|eliminat(?:e|es)|erase[sd]?|hilang(?:kan)?)\b[\s\w]{0,12}\b(scars?|parut|stretch\s*marks?)\b",
         "Claims removal of scars rather than improving their appearance.",
         f"{PART8}, Skin products: 'Remove / eliminate scars' unacceptable; 'Reduce or improve the appearance of scars' acceptable."),
    Rule("hair_regrowth", "medicinal",
         r"\b(stimulat(?:e|es|ing)|promot(?:e|es|ing)|regrow(?:s|th)?|tumbuh(?:kan)?\s+(?:semula\s+)?rambut|menumbuhkan)\b[\s\w]{0,12}\b(hair\s+growth|hair|rambut|follicle)\b|\b(alopecia|baldness|botak|kebotakan)\b",
         "Hair growth stimulation / baldness claims.",
         f"{PART8}, Hair and scalp care: 'Stimulate / promote hair growth', baldness, alopecia unacceptable."),
    Rule("slimming", "medicinal",
         r"\b(slim(?:ming)?|fat\s*(?:burn|loss)|inch\s*loss|kurus(?:kan)?|bakar\s+lemak|detox|drainage|oedema|edema|swelling|bengkak)\b",
         "Slimming, fat loss, fluid drainage claims are outside cosmetic scope.",
         f"{PART8}, Skin products: slimming, fat loss, removal of excess body fluid unacceptable."),
    Rule("pain_relief", "medicinal",
         r"\b(reliev(?:e|es)|hilangkan|redakan|lega(?:kan)?)\b[\s\w]{0,10}\b(pain|ache|sakit|migraine|migrain)\b|\b(anti-?septic|antiseptik|numb(?:ing)?|kebas)\b",
         "Pain relief / numbing / antiseptic wording.",
         f"{PART8}: reference to pain or ache, numbing effect on the skin, antiseptic wording unacceptable."),

    # Mechanism (dermis and below)
    Rule("collagen_elastin", "mechanism",
         r"\b(collagen|kolagen|elastin)\b[\s\w]{0,20}\b(production|produce|synthesis|boost(?:s|ing)?|stimulat(?:e|es|ing)|increase[sd]?|hasil(?:kan)?|rangsang(?:kan)?|tingkat(?:kan)?)\b|\b(boost(?:s|ing)?|stimulat(?:e|es|ing)|increase[sd]?|rangsang(?:kan)?|tingkat(?:kan)?)\b[\s\w]{0,15}\b(collagen|kolagen|elastin)\b",
         "Claims an effect on collagen / elastin production (dermis).",
         f"{PART8}, Skin products: reference to production of collagen and elastin unacceptable."),
    Rule("melanin", "mechanism",
         r"\b(melanin)\b[\s\w]{0,15}\b(inhibit|block|stop|suppress|reduce|control|halang|sekat|kawal)|\b(inhibit|block|stop|suppress|halang|sekat)\w*\b[\s\w]{0,15}\b(melanin)\b",
         "Claims inhibition of melanin synthesis.",
         f"{PART8}, Skin products: inhibition of melanin synthesis unacceptable; 'reduce / fade dark spots' acceptable."),
    Rule("dna_cells", "mechanism",
         r"\b(dna\s+repair|repair(?:s|ing)?\s+dna|baiki\s+dna|cell(?:ular)?\s+(?:regenerat|repair|renewal\s+at|level|deep)\w*|deep\s+cellular|regenerat\w*\s+(?:skin\s+)?cells?|skin\s+metabolism|metabolisme\s+kulit|stem\s*cells?|sel\s+stem|blood\s+(?:micro)?circulation|peredaran\s+darah|microcirculation)\b",
         "Claims DNA repair, cellular regeneration, skin metabolism or blood circulation.",
         f"{PART8}, Skin products: DNA repair, skin metabolism, blood circulation / microcirculation unacceptable."),
    Rule("hormonal", "mechanism",
         r"\b(hormon(?:e|al|es)?|hormon)\b",
         "Hormonal reference.",
         f"{PART8}, Others: graphics or references to hormone, internal organs or substances of human origin unacceptable."),

    # Professional endorsement — Part 10 s.4.1
    Rule("derm_approved", "endorsement",
         r"\b(dermatologist|dermatologis|doctor|doktor|dr\.?|pharmacist|ahli\s+farmasi|pakar\s+kulit|physician)s?\b[\s\w\-]{0,12}\b(approved|recommend(?:ed|s)?|endorsed|prescrib\w*|trusted|loved\s+by|choice|pick|syor(?:kan)?|cadang(?:kan)?|lulus(?:kan)?|percaya)\b|\b(approved|recommended|loved|trusted)\s+by\s+(?:\w+\s+){0,3}(dermatologists?|doctors?|pharmacists?|doktor|pakar\s+kulit)\b|\b(derm[\s\-]?approved|derm[\s\-]?recommended|dermatologist\s+care|doctor'?s?\s+choice)\b",
         "Doctor / dermatologist / pharmacist recommendation or approval.",
         f"{PART10}, s.4.1 Impressions of professional advice or endorsement — not permitted; 'Dermatologically tested' is the accepted form."),
    Rule("hospital_clinic", "endorsement",
         r"\b(hospital|clinic|klinik|clinically\s+proven|terbukti\s+secara\s+klinikal|klinikal\s+terbukti)\b",
         "Hospital / clinic reference or 'clinically proven' (needs the study).",
         f"{PART10}, s.4.1 no reference to a hospital or similar establishment; s.7 tests and trials only if fully substantiated.",
         severity="risky"),

    # Sunscreen — Part 8 sunscreen table + Part 9(i)
    Rule("sun_words", "sunscreen",
         r"\b(sun\s*block|sunblock|uv\s*(?:block|cut)|water\s*proof|sweat\s*proof|kalis\s+air|kalis\s+peluh|100\s*%\s*(?:uv\s+)?protection|all[\s\-]?day\s+protection|spf\s*(?:[6-9]\d|1\d\d)\b(?!\+))",
         "Sunblock / UV block / waterproof / sweatproof / 100% or all-day protection / SPF above 50 stated as a number.",
         f"{PART8}, Sunscreen: sunblock, sweat proof / water proof, UV block / UV cut, SPF above 50 unacceptable; use water resistant, UV filter, SPF 50+."),

    # Safety
    Rule("no_side_effects", "safety",
         r"\b(no|zero|without|tanpa|tiada|bebas)\s+(?:\w+\s+){0,2}(side[\s\-]?effects?|kesan\s+sampingan|harmful\s+effects?|adverse|toxic)\b|\b(100\s*%\s*(?:safe|selamat)|totally\s+safe|completely\s+safe|selamat\s+sepenuhnya)\b",
         "Claims freedom from side effects or absolute safety.",
         f"{PART8}, s.3 Safety claims: must not imply the product is free from side effects; 'no side effects', 'no harmful effects' not allowed."),

    # Absolutes / permanence
    Rule("permanent", "absolute",
         r"\b(permanent(?:ly)?|kekal|selama-?lamanya|forever|reverse[sd]?\s+(?:the\s+)?ag(?:e)?ing|stop(?:s)?\s+ag(?:e)?ing|anti-?ag(?:e)?ing\s+cure|100\s*%\s*(?:result|effective|berkesan)|guaranteed?|dijamin|jaminan)\b",
         "Permanent, guaranteed, reverse-ageing or 100% wording.",
         f"{PART8}, Skin products: prevent / reverse / delay the ageing process unacceptable; 5-step process gate 5 (no permanent physiological change); s.3 quantitative / absolute claims need substantiation.",
         severity="risky"),

    # Comparison — Part 10 s.5.1
    Rule("comparison", "comparison",
         r"\b(better\s+than|lebih\s+baik\s+(?:dari|daripada)|unlike\s+other|tidak\s+seperti|don'?t\s+settle|ordinary\s+(?:brands?|products?)|jenama\s+lain|competitor|pesaing|no\.?\s*1|number\s+one|#1|the\s+only|satu-?satunya)\b",
         "Implied or direct comparison / superlative.",
         f"{PART10}, s.5.1 Disparagement and denigration; s.8 hyperbole and superlatives only where substantiated.",
         severity="risky"),

    # Before / after — Part 10 s.5.2
    Rule("before_after", "before_after",
         r"\b(before\s*(?:&|and|/)\s*after|sebelum\s*(?:&|dan|/)\s*selepas|b/a\b|week\s*0|minggu\s*0)\b",
         "Before/after presentation — the time elapsed must be stated with prominence.",
         f"{PART10}, s.5.2 Before and after effects: must cite with prominence the specific time elapsed.",
         severity="risky"),

    # GMP / MOH
    Rule("gmp_moh", "gmp_moh",
         r"\b(gmp|iso\s*22716|kkm\s+approved|approved\s+by\s+(?:the\s+)?(?:ministry\s+of\s+health|moh|kkm)|diluluskan\s+(?:oleh\s+)?kkm|npra\s+approved|kelulusan\s+kkm|moh\s+approved|halal\s+certified)\b",
         "GMP / MOH / KKM approval reference (halal only if certified — check).",
         f"{PART8}, Others: GMP logo or certification, 'Approved by Ministry of Health' unacceptable. Note: cosmetics are notified, not approved."),

    # Prohibited ingredient / procedure references
    Rule("prohibited_ref", "prohibited_ref",
         r"\b(cannabis|hemp|cbd|o?estrogen|progesterone|egf|fgf|growth\s+factors?|mesotherapy|microneedl\w*|derma-?roller|injection|suntik\w*|botox|filler|liposuction|susuk|cosmeceutical)\b",
         "Prohibited ingredient or medical-procedure reference.",
         f"{PART8}, Others: cosmeceutical, mesotherapy, injection, microneedling, derma-roller, susuk, EGF / FGF references unacceptable; NPRA 2025 FAQ lists cannabis / hemp / hormones as serious violations."),

    # Religious / supernatural
    Rule("religious", "other",
         r"\b(hadith|al-?quran|quran|bible|sihir|saka|badi|jampi|mujarab)\b",
         "Religious or supernatural reference.",
         f"{PART8}, Others: reference to Hadith, Al-Quran, Bible or supernatural / superstitious elements unacceptable; {PART10} s.4.4."),

    # Quantitative claims (risky until the study is seen)
    Rule("numbers", "quantitative",
         r"\b(\d{1,3}\s*%|\d+\s*x\b|\bin\s+\d+\s+(?:days?|hours?|weeks?|minutes?)|dalam\s+\d+\s+(?:hari|jam|minggu|minit)|\d+\s+(?:hari|jam|minggu|minit))",
         "Numerical performance claim — acceptable only if substantiated.",
         f"{PART8}, s.3 Quantitative claims: acceptable if substantiated by relevant evidence.",
         severity="risky"),
]

# Claims that look alarming but sit inside the acceptable column; the LLM is told
# about them so it does not over-flag. Checked here only to annotate hits.
ACCEPTABLE_HINTS = [
    r"\b(prevent|control|reduce|kurangkan|kawal|cegah)\b[\s\w]{0,10}\b(acne|jerawat|breakouts?)\b",
    r"\b(reduce|improve|kurangkan)\b[\s\w]{0,6}\b(the\s+)?appearance\s+of\b",
    r"\b(repair|restore|baiki|pulih(?:kan)?)\b[\s\w]{0,6}\b(skin\s+)?barrier\b",
    r"\bdermatologically\s+tested\b|\bdiuji\s+secara\s+dermatologi\b",
    r"\bwater[\s\-]?resistant\b|\bkalis\s+air\b.*\bsolek",
    r"\bspf\s*50\+",
]
_ACCEPTABLE = [re.compile(p, re.IGNORECASE) for p in ACCEPTABLE_HINTS]


def _context(text: str, start: int, end: int, width: int = 60) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    return ("…" if a > 0 else "") + text[a:b].replace("\n", " ") + ("…" if b < len(text) else "")


def prescreen(text: str) -> List[Hit]:
    """Return every candidate hit in the text (deduplicated by rule + match)."""
    if not text:
        return []
    hits: List[Hit] = []
    seen = set()
    for rule in RULES:
        for m in rule._rx.finditer(text):
            key = (rule.key, m.group(0).lower())
            if key in seen:
                continue
            seen.add(key)
            hits.append(Hit(
                rule_key=rule.key, category=CATEGORIES[rule.category], matched=m.group(0),
                context=_context(text, m.start(), m.end()), reason=rule.reason,
                citation=rule.citation, severity=rule.severity,
            ))
    return hits


def acceptable_hints(text: str) -> List[str]:
    return [m.group(0) for rx in _ACCEPTABLE for m in rx.finditer(text or "")]


def offline_verdict(text: str) -> dict:
    """
    Verdict without an LLM. Any 'unacceptable' pattern hit → Unacceptable; only
    'risky' hits → Risky; nothing → Acceptable. Confidence is deliberately modest:
    a regex cannot read the full sentence, which Part 8 review requires.
    """
    hits = prescreen(text)
    unaccept = [h for h in hits if h.severity == "unacceptable"]
    risky = [h for h in hits if h.severity == "risky"]
    if unaccept:
        top = unaccept[0]
        return {
            "verdict": "Unacceptable", "confidence": 0.55,
            "violation_type": top.category,
            "violation_reason": "; ".join(f'"{h.matched}" — {h.reason} [{h.citation}]' for h in unaccept[:4]),
            "claims": [{"claim": h.matched, "verdict": "Unacceptable", "reference": h.citation,
                        "reason": f"{h.reason} In: {h.context}", "source": "caption"} for h in unaccept],
            "product_name": "",
            "complaint_description_bm": "",
            "reviewer": "rules-only",
        }
    if risky:
        top = risky[0]
        return {
            "verdict": "Risky", "confidence": 0.4, "violation_type": top.category,
            "violation_reason": "; ".join(f'"{h.matched}" — {h.reason} [{h.citation}]' for h in risky[:4]),
            "claims": [{"claim": h.matched, "verdict": "Risky", "reference": h.citation,
                        "reason": f"{h.reason} In: {h.context}", "source": "caption"} for h in risky],
            "product_name": "", "complaint_description_bm": "", "reviewer": "rules-only",
        }
    return {"verdict": "Acceptable", "confidence": 0.5, "violation_type": "", "violation_reason": "",
            "claims": [], "product_name": "", "complaint_description_bm": "", "reviewer": "rules-only"}


# Condensed rule text handed to the LLM reviewer as its rulebook.
RULEBOOK = f"""
GOVERNING INSTRUMENTS (cite exactly these):
- {PART8}, Guidelines for Control of Cosmetic Products in Malaysia, 2nd ed., NPRA, Aug 2022.
- {PART10}, same guideline, Aug 2022.
- Regulation 18A, Control of Drugs and Cosmetics Regulations 1984 (basis of NPRA enforcement letters).

PART 8 — 5-step cosmetic test: a cosmetic (1) uses permitted ingredients, (2) is applied externally,
(3) mainly cleans / perfumes / changes appearance / corrects odour / protects, (4) is NOT presented as
treating or preventing disease, (5) does NOT permanently restore, correct or modify a physiological
function. Failing (4) or (5) = medicinal claim = Unacceptable.

PART 8 — Skin products, UNACCEPTABLE: production of collagen / elastin; inhibition of melanin synthesis;
DNA repair; skin metabolism; blood (micro)circulation; prevent / reduce / reverse / delay the ageing
process; heals, treats or stops acne; treatment of skin conditions (pigmentation, hyperpigmentation,
freckles, melasma, acne, fragile capillaries, rosacea); treatment of warts; prevent or treat cellulite;
remove / eliminate scars; draining / oedema / swelling; slimming / fat loss / inch loss / body
metabolism; treatment of compromised skin (bruises, wounds, burns); numbing effect; reference to skin
diseases (eczema, psoriasis, atopic dermatitis, vitiligo, varicose vein, rash, prickly heat, nappy rash).
ACCEPTABLE: helps nourish / rejuvenate / regenerate cells (appearance level); increase cell turnover /
skin renewal; slows down / delays SIGNS of ageing; prevent / control / reduce acne / breakout; prevent /
reduce dark spot, acne mark, wrinkle, pigmentation, stretch mark; reduce the APPEARANCE of cellulite or
scars; body shaping / firming; soften hard skin, corn, callous, cracked heel.

PART 8 — Hair: UNACCEPTABLE stimulate / promote hair growth, restores hair cells, hereditary / hormonal
hair loss, baldness, alopecia, prevent grey hair, cradle cap / seborrheic dermatitis. ACCEPTABLE
anti-hair-fall due to breakage, promote healthy hair, strengthen / nourish hair root.

PART 8 — Sunscreen: UNACCEPTABLE sunblock, sweat proof / water proof, UV block / UV cut, skin cancer,
SPF stated above 50 (e.g. SPF 130), 100% protection. ACCEPTABLE water / sweat resistant, prevent
sunburn, UV filter, protect against UVA & UVB, SPF 50+. Waterproof IS acceptable for makeup.

PART 8 — Others: UNACCEPTABLE antimicrobial (antibacterial is fine), disinfectant, fungicidal / virucidal,
religious references, supernatural (saka, sihir, badi, penawar), insect repellent, cosmeceutical,
mesotherapy, injection, microneedling, derma-roller, susuk, 100% protection, GMP logo / certification,
"Approved by Ministry of Health", images of internal organs / hormones / growth factors (EGF, FGF),
"medicated". Safety claims: no "no side effects", "no harmful effects", natural ≠ safe.
Quantitative claims (99.9%, in 3 days, 10x): ACCEPTABLE ONLY IF SUBSTANTIATED → Risky by default.

PART 10 — s.4.1 no doctor / dentist / pharmacist / dermatologist endorsement or impression of it (white
coat, stethoscope, clinic, "Dr", "dermatologist recommended / approved", "loved by dermatologists",
expert Q&A formats), no hospital reference; "Dermatologically tested" is the accepted form.
s.4.3 all claims must be substantiable. s.4.4 no fear, superstition, religion. s.5.1 no direct comparison
with a competitor, no disparagement, including implied ("don't settle", "unlike ordinary…").
s.5.2 before / after must state the time elapsed prominently. s.6 testimonials must be genuine.
s.7 tests / trials only if substantiated; named institutions need consent. s.8 superlatives only if
substantiated.

HOUSE RULES (team playbook, use for rewrites; say when a house position is doing the work):
- Skin-layer test: epidermis claims are claimable; dermis and below (collagen, elastin, sebum
  PRODUCTION, blood circulation, fat, muscle) are not. Inhibiting melanin is not claimable.
- "Repair" / "restore" ARE accepted for the skin BARRIER (stratum corneum). "Balance the microbiome" is
  not; "maintain a healthy microbiome" is.
- Swaps: treat/cure/heal → reduce / relieve / target; inflammation → irritation; melanin → pigmentation;
  collagen → skin elasticity; sebum production → sebum; dermatologist approved → dermatologically tested.
- Precedent: NPRA has warned for "merawat jerawat", "heal your skin", "solves all acne problems",
  doctor-KOL posts (paid or not), "Loved by U.S. Board-Certified Dermatologists", "wound healing".
  Product shown entering the mouth = proposed cancellation, maximum severity.

VERDICT SCALE: Acceptable | Risky (depends on substantiation or context; say what would clear it) |
Unacceptable (matches a prohibited claim / presentation). Judge the FULL SENTENCE, not the isolated
word. Quote claims exactly as written, original language. Never invent a clause number: if no specific
row applies, say the verdict rests on the general principle (truthful, substantiated, within cosmetic
scope) and Part 10 s.4.3.
""".strip()
