"""
LLM claims reviewer — acts as an NPRA Claims Reviewer over the extracted text (and the
screenshot, so on-image claims count) and returns a structured verdict.

Provider: Anthropic (default, `claude-opus-5`) or OpenAI. Selected by LLM_PROVIDER.
Falls back to the deterministic rule engine when no key is configured or the API fails.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

from npra_rules import RULEBOOK, acceptable_hints, offline_verdict, prescreen

log = logging.getLogger("kkm.evaluator")


def env_str(name: str, default: str = "") -> str:
    """Same tolerance for inline comments as the scraper. See scraper.env_str."""
    v = (os.getenv(name) or "").strip()
    if v.startswith("#"):
        return default
    v = v.split("  #", 1)[0].split("\t#", 1)[0].strip()
    return v or default

# USD per million tokens, from the Anthropic pricing page. Update when prices change.
# Cache reads bill at ~0.1x input, cache writes at ~1.25x input.
MODEL_PRICES = {
    "claude-fable-5-1":  (10.0, 50.0),
    "claude-fable-5":    (10.0, 50.0),
    "claude-opus-5":     (5.0, 25.0),
    "claude-opus-4-8":   (5.0, 25.0),
    "claude-opus-4-7":   (5.0, 25.0),
    "claude-opus-4-6":   (5.0, 25.0),
    "claude-sonnet-5":   (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5":  (1.0, 5.0),
}

# output_config.effort is rejected by Haiku 4.5 and the 4.5-generation models.
def _supports_effort(model: str) -> bool:
    m = (model or "").lower()
    return not ("haiku" in m or "-4-5" in m)


def price_for(model: str):
    m = (model or "").lower()
    if m in MODEL_PRICES:
        return MODEL_PRICES[m]
    for key, val in MODEL_PRICES.items():  # tolerate suffixes or aliases
        if m.startswith(key):
            return val
    return None


def usd_cost(model: str, usage: dict) -> float:
    p = price_for(model)
    if not p or not usage:
        return 0.0
    inp, out = p
    fresh = usage.get("input_tokens", 0) or 0
    cread = usage.get("cache_read_input_tokens", 0) or 0
    cwrite = usage.get("cache_creation_input_tokens", 0) or 0
    otok = usage.get("output_tokens", 0) or 0
    return ((fresh + cwrite * 1.25 + cread * 0.1) * inp + otok * out) / 1_000_000


VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["Acceptable", "Risky", "Unacceptable"]},
        "confidence": {"type": "number", "description": "0 to 1: probability an NPRA officer would agree with the verdict"},
        "violation_type": {"type": "string", "enum": [
            "Medicinal / disease claim",
            "Mechanism claim (collagen, melanin, DNA, cells)",
            "Professional endorsement (doctor / dermatologist)",
            "Prohibited sunscreen wording",
            "Safety / no-side-effect claim",
            "Absolute / permanent result",
            "Comparison or disparagement",
            "Before-after without time elapsed",
            "GMP / MOH / approval reference",
            "Prohibited ingredient or procedure reference",
            "Unsubstantiated quantitative claim",
            "Other",
            ""
        ]},
        "violation_reason": {"type": "string"},
        "product_name": {"type": "string"},
        "post_date": {"type": "string", "description": "Date the post was published, YYYY-MM-DD, only if visible in the screenshot or text; else \"\""},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "verdict": {"type": "string", "enum": ["Acceptable", "Risky", "Unacceptable"]},
                    "reference": {"type": "string"},
                    "reason": {"type": "string"},
                    "source": {"type": "string", "enum": ["caption", "image", "hashtag", "product_name"]}
                },
                "required": ["claim", "verdict", "reference", "reason", "source"],
                "additionalProperties": False
            }
        },
        "complaint_description_bm": {"type": "string"},
        "notes": {"type": "string"}
    },
    "required": ["verdict", "confidence", "violation_type", "violation_reason", "product_name", "post_date",
                 "claims", "complaint_description_bm", "notes"],
    "additionalProperties": False
}

SYSTEM_PROMPT = f"""You are an NPRA Claims Reviewer for the Ministry of Health Malaysia (KKM) cosmetic
post-market surveillance programme. You screen social-media posts by cosmetic brands against the
Guidelines for Control of Cosmetic Products in Malaysia. You are precise, cite the actual rule, and
never invent clause numbers.

{RULEBOOK}

METHOD
1. Break the post into individual claims: caption sentences, hashtags, the product name, and any text or
   visual claim in the screenshot (before/after, white coat, "Dr", percentages on screen, product near the
   mouth). A claim delivered by picture is still a claim.
2. Judge each claim on the full sentence against the tables above. Appearance-level wording passes;
   mechanism-level and disease-level wording fails. Category matters (waterproof: makeup OK, sunscreen not).
3. The overall verdict is the worst claim. Unacceptable only when at least one claim matches a prohibited
   claim or presentation. Risky when it hinges on substantiation or context (numbers, "clinically proven",
   superlatives, before/after with a time stated elsewhere). Acceptable otherwise. Do not over-flag:
   "reduce acne", "dermatologically tested", "repairs skin barrier", "SPF 50+", "water resistant",
   "fade dark spots", "soothes irritation" are acceptable.
4. Non-cosmetic or purely informational posts (events, giveaways with no claims, brand greetings) are
   Acceptable with an empty violation_type.
5. violation_reason: one paragraph, quoting each failing claim exactly as written, with the citation
   (e.g. 'Annex I Part 8, Skin products: "Heals, treats or stops acne" — unacceptable'). This text goes
   into a regulator complaint, so no speculation and no facts you cannot see in the post.
6. product_name: the product as named in the post, else "".
6b. post_date: the publication date of the post as shown by the platform (timestamp under the page name in the
   screenshot, or a dated line in the text), as YYYY-MM-DD. Not an event or promotion date. "" if not visible.
7. complaint_description_bm: when the verdict is Unacceptable, write the "Deskripsi Aduan" for the KKM
   form in Bahasa Malaysia (Malaysia, never Indonesia: ubat not obat, syarikat not perusahaan, kualiti
   not kualitas). Structure: apa yang diiklankan · dakwaan tepat (petik) · kenapa tidak dibenarkan
   (rujukan Annex I Part 8 / Part 10) · pautan dan tangkapan skrin dilampirkan. Where the notification
   number is unknown write "[SAHKAN: semak QUEST3+]". Otherwise "".
8. confidence: your probability that an NPRA officer would agree with the verdict.
Return only the JSON object."""


@dataclass
class ReviewInput:
    brand: str
    platform: str
    url: str
    text: str
    screenshot_path: Optional[str] = None
    product_hints: Optional[list] = None
    target_type: str = ""   # "" / "Brand" for a brand's own page; "Doctor" or "KOL" for a promoter


def _user_prompt(inp: ReviewInput) -> str:
    hits = prescreen(inp.text)
    ok = acceptable_hints(inp.text)
    hint_block = "\n".join(f'- [{h.severity}] "{h.matched}" → {h.category}: {h.reason} ({h.citation})' for h in hits[:20]) or "- none"
    ok_block = "\n".join(f'- "{s}"' for s in ok[:10]) or "- none"
    products = ", ".join(inp.product_hints or []) or "unknown"
    kind = (inp.target_type or "").strip().lower()
    if kind and kind not in ("brand", "own", "company"):
        account = (f"\nACCOUNT TYPE: {inp.target_type} — this account is not the brand. It is a person "
                   "promoting cosmetic products. Judge the post as an advertisement carried by that "
                   "person: Part 10 s.4.1 makes a doctor / dentist / pharmacist / dermatologist "
                   "endorsement, or the impression of one (title, white coat, clinic setting, "
                   "credentials in the bio or caption), unacceptable for a cosmetic — whether or not "
                   "the post is paid. s.6 requires a testimonial to be genuine. The claims tables "
                   "apply exactly as they do to a brand's own post.")
    else:
        account = ""
    return f"""BRAND: {inp.brand}{account}
PLATFORM: {inp.platform}
POST URL: {inp.url}
KNOWN PRODUCT LINES (for product_name only, do not assume the post is about them): {products}

POST TEXT (caption, alt text, on-page text; may be BM-English mix):
\"\"\"
{inp.text.strip() or '(no text extracted — judge the screenshot)'}
\"\"\"

RULE-ENGINE CANDIDATES (regex hits; verify each on the full sentence, they are not verdicts):
{hint_block}

PHRASES THAT LOOK LIKE ACCEPTABLE COLUMN WORDING (do not flag these unless the sentence changes them):
{ok_block}

If a screenshot is attached, read every piece of text on it and assess visual claims too."""


def _image_block_anthropic(path: str) -> Optional[dict]:
    try:
        with open(path, "rb") as f:
            data = base64.standard_b64encode(f.read()).decode("utf-8")
        mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
        return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
    except OSError as e:
        log.warning("screenshot unreadable for LLM: %s", e)
        return None


def _review_anthropic(inp: ReviewInput) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    model = env_str("ANTHROPIC_MODEL", "claude-haiku-4-5")
    content = []
    if inp.screenshot_path and env_str("LLM_USE_SCREENSHOT", "1") == "1":
        blk = _image_block_anthropic(inp.screenshot_path)
        if blk:
            content.append(blk)
    content.append({"type": "text", "text": _user_prompt(inp)})

    output_config = {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}
    if _supports_effort(model):
        output_config["effort"] = env_str("ANTHROPIC_EFFORT", "high")
    kwargs = dict(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config=output_config,
    )

    use_fallbacks = env_str("ANTHROPIC_FALLBACKS", "1") == "1"
    try:
        if use_fallbacks:
            # Server-side refusal fallback: a policy decline re-runs on a fallback model in the same call.
            response = client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = client.messages.create(**kwargs)
    except anthropic.BadRequestError as e:
        if use_fallbacks:
            log.warning("fallbacks parameter rejected (%s); retrying without it", e.message)
            response = client.messages.create(**kwargs)
        else:
            raise

    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the request (stop_reason=refusal)")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    served = getattr(response, "model", model) or model
    data["reviewer"] = f"anthropic:{served}"
    u = getattr(response, "usage", None)
    if u is not None:
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        usage["model"] = served
        usage["usd"] = round(usd_cost(served, usage), 6)
        data["usage"] = usage
    return data


def _review_openai(inp: ReviewInput) -> dict:
    """
    The OpenAI-compatible path. OPENAI_BASE_URL aims it at any gateway that speaks
    /v1/chat/completions — rootsys, OpenRouter, a local server — so GLM, Kimi, DeepSeek and
    MiniMax are reachable through this same code.

    Gateways differ in what they accept, so two things degrade instead of failing the run:
    strict json_schema falls back to json_object and then to a plain request, and a model
    that will not take an image is retried on text alone (loudly — most violations here are
    on the artwork, not in the caption).
    """
    from openai import OpenAI

    base_url = env_str("OPENAI_BASE_URL")
    client = OpenAI(base_url=base_url) if base_url else OpenAI()
    model = env_str("OPENAI_MODEL", "gpt-4.1")
    where = f" via {base_url}" if base_url else ""

    text_part = {"type": "text", "text": _user_prompt(inp)}
    image_part = None
    if inp.screenshot_path and env_str("LLM_USE_SCREENSHOT", "1") == "1":
        try:
            with open(inp.screenshot_path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            mime = "image/png" if inp.screenshot_path.lower().endswith(".png") else "image/jpeg"
            image_part = {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        except OSError as e:
            log.warning("screenshot unreadable for LLM: %s", e)

    json_modes = [
        {"type": "json_schema", "json_schema": {"name": "npra_verdict", "strict": True, "schema": VERDICT_SCHEMA}},
        {"type": "json_object"},
        None,
    ]
    resp = None
    last_err = None
    for with_image in ([True, False] if image_part else [False]):
        content = [text_part] + ([image_part] if with_image else [])
        for mode in json_modes:
            kwargs = {"model": model, "temperature": 0,
                      "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                   {"role": "user", "content": content}]}
            if mode:
                kwargs["response_format"] = mode
            try:
                resp = client.chat.completions.create(**kwargs)
                break
            except Exception as e:
                last_err = e
                log.info("reviewer%s: %s rejected %s (%s)", where, model,
                         (mode or {}).get("type", "plain request"), str(e)[:160])
        if resp is not None:
            if not with_image and image_part:
                log.warning("reviewer%s: %s would not take the screenshot; judged on the caption alone, "
                            "so claims made on the artwork were not seen", where, model)
            break
    if resp is None:
        raise RuntimeError(f"reviewer{where}: {model} rejected every request shape: {last_err}")

    raw = resp.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:   # no JSON mode: the object arrives wrapped in a fence or prose
        stripped = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        m = re.search(r"\{.*\}", stripped, re.S)
        if not m:
            raise
        data = json.loads(m.group(0))
    data["reviewer"] = f"openai:{model}"
    u = getattr(resp, "usage", None)
    if u is not None:
        # No price table for OpenAI here: tokens are reported, cost is left at 0 rather than guessed.
        data["usage"] = {"model": model, "input_tokens": getattr(u, "prompt_tokens", 0) or 0,
                         "output_tokens": getattr(u, "completion_tokens", 0) or 0,
                         "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "usd": 0.0}
    return data


def _normalise(data: dict) -> dict:
    data.setdefault("claims", [])
    data.setdefault("notes", "")
    data.setdefault("usage", None)
    data.setdefault("product_name", "")
    data.setdefault("complaint_description_bm", "")
    pd = str(data.get("post_date") or "").strip()
    data["post_date"] = pd if len(pd) == 10 and pd[4] == "-" and pd[7] == "-" else ""
    try:
        data["confidence"] = min(1.0, max(0.0, float(data.get("confidence") or 0)))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    if data.get("verdict") == "Acceptable":
        data["violation_type"] = ""
        data["violation_reason"] = data.get("violation_reason") or ""
    elif not data.get("violation_type"):
        data["violation_type"] = "Other"
    return data


def review(inp: ReviewInput, use_llm: bool = True, provider: Optional[str] = None) -> dict:
    """
    Returns a dict with verdict / confidence / violation_type / violation_reason / product_name /
    claims / complaint_description_bm / reviewer.
    Rule engine short-circuit: when the regex bank finds nothing at all and there is no
    screenshot, the post is Acceptable without an LLM call (saves the bulk of the spend).
    `provider` overrides LLM_PROVIDER for this one call, so the same post can be put to two
    reviewers and their verdicts compared.
    """
    provider = (provider or env_str("LLM_PROVIDER", "anthropic")).lower()
    has_key = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")) if provider == "anthropic" else bool(os.getenv("OPENAI_API_KEY"))
    if not use_llm or not has_key:
        if use_llm and not has_key:
            log.warning("no LLM key for provider=%s; using rules-only verdict", provider)
        return _normalise(offline_verdict(inp.text))

    if not prescreen(inp.text) and not inp.screenshot_path and len(inp.text or "") < 2000:
        v = offline_verdict(inp.text)
        v["reviewer"] = "rules-only (no candidates)"
        return _normalise(v)

    try:
        data = _review_anthropic(inp) if provider == "anthropic" else _review_openai(inp)
        return _normalise(data)
    except Exception as e:  # any provider failure → rules fallback, never a crashed run
        log.error("LLM review failed for %s: %s — falling back to rules", inp.url, e)
        v = offline_verdict(inp.text)
        v["notes"] = f"LLM failed: {e}"
        return _normalise(v)
