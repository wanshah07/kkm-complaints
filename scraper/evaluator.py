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
from dataclasses import dataclass
from typing import Optional

from npra_rules import RULEBOOK, acceptable_hints, offline_verdict, prescreen

log = logging.getLogger("kkm.evaluator")

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
    "required": ["verdict", "confidence", "violation_type", "violation_reason", "product_name",
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


def _user_prompt(inp: ReviewInput) -> str:
    hits = prescreen(inp.text)
    ok = acceptable_hints(inp.text)
    hint_block = "\n".join(f'- [{h.severity}] "{h.matched}" → {h.category}: {h.reason} ({h.citation})' for h in hits[:20]) or "- none"
    ok_block = "\n".join(f'- "{s}"' for s in ok[:10]) or "- none"
    products = ", ".join(inp.product_hints or []) or "unknown"
    return f"""BRAND: {inp.brand}
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
    model = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
    content = []
    if inp.screenshot_path and os.getenv("LLM_USE_SCREENSHOT", "1") == "1":
        blk = _image_block_anthropic(inp.screenshot_path)
        if blk:
            content.append(blk)
    content.append({"type": "text", "text": _user_prompt(inp)})

    kwargs = dict(
        model=model,
        max_tokens=4000,
        system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": VERDICT_SCHEMA}},
    )

    use_fallbacks = os.getenv("ANTHROPIC_FALLBACKS", "1") == "1"
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
    data["reviewer"] = f"anthropic:{getattr(response, 'model', model)}"
    return data


def _review_openai(inp: ReviewInput) -> dict:
    from openai import OpenAI

    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    content = [{"type": "text", "text": _user_prompt(inp)}]
    if inp.screenshot_path and os.getenv("LLM_USE_SCREENSHOT", "1") == "1":
        try:
            with open(inp.screenshot_path, "rb") as f:
                b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            mime = "image/png" if inp.screenshot_path.lower().endswith(".png") else "image/jpeg"
            content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
        except OSError as e:
            log.warning("screenshot unreadable for LLM: %s", e)
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": content}],
        response_format={"type": "json_schema", "json_schema": {"name": "npra_verdict", "strict": True, "schema": VERDICT_SCHEMA}},
        temperature=0,
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    data["reviewer"] = f"openai:{model}"
    return data


def _normalise(data: dict) -> dict:
    data.setdefault("claims", [])
    data.setdefault("notes", "")
    data.setdefault("product_name", "")
    data.setdefault("complaint_description_bm", "")
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


def review(inp: ReviewInput, use_llm: bool = True) -> dict:
    """
    Returns a dict with verdict / confidence / violation_type / violation_reason / product_name /
    claims / complaint_description_bm / reviewer.
    Rule engine short-circuit: when the regex bank finds nothing at all and there is no
    screenshot, the post is Acceptable without an LLM call (saves the bulk of the spend).
    """
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
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
