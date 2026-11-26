"""Open every cited source and check that it says what the reason quotes.

The judges score evidence on exactly this, so no reason reaches the
submission file without having been re-opened by code, not by the model.
"""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from .collect import fetch, page_text

# ok: quote found. weak: page loads but only part of the quote matches.
# unreadable: the page refused us or renders client-side, so we could not tell.
# missing: the page loads as plain text and the quote is not on it. dead: the URL does not load.


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}))
    return " ".join(re.sub(r"[^\w\s'\"%€$.-]", " ", s).split())


def _overlap(quote: str, text: str) -> float:
    """Share of the quote's 4-word runs that appear in the page."""
    words = quote.split()
    if len(words) < 4:
        return 1.0 if quote in text else 0.0
    grams = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    return sum(g in text for g in grams) / len(grams)


def check_reason(reason: dict) -> dict:
    url, quote = reason.get("source_url", ""), _norm(reason.get("quote", ""))
    if not url.startswith("http"):
        return {"status": "dead", "detail": "not a URL"}
    r = fetch(url)
    if r["status"] in (0, 404, 410):
        return {"status": "dead", "detail": r.get("error") or f"HTTP {r['status']}"}
    if r["status"] != 200:
        return {"status": "unreadable", "detail": f"HTTP {r['status']}"}
    if r["headers"].get("Content-Type", "").startswith("application/json") or r["text"].lstrip().startswith(("{", "[")):
        text = _norm(r["text"])
    else:
        text = _norm(page_text(r["text"]))
    if not quote:
        return {"status": "weak", "detail": "no quote given"}
    if len(quote.split()) < 4:
        return {"status": "weak", "detail": "quote too short to show anything; quote the sentence that states the fact"}
    if quote in text:
        return {"status": "ok", "detail": ""}
    score = _overlap(quote, text)
    if score >= 0.6:
        return {"status": "ok", "detail": f"{score:.0%} of the quote matched"}
    if len(text) < 1500:
        return {"status": "unreadable", "detail": "page renders client-side; could not read its text"}
    if score >= 0.25:
        return {"status": "weak", "detail": f"only {score:.0%} of the quote matched"}
    return {"status": "missing", "detail": "quote not found on the page"}


def check_all(reasons: list[dict]) -> list[dict]:
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(check_reason, reasons))
    for reason, res in zip(reasons, results):
        reason["check"] = res
    return reasons


def failed(reasons: list[dict]) -> list[dict]:
    return [{"claim": r["claim"], "source_url": r["source_url"], "quote": r["quote"],
             "problem": f"{r['check']['status']}: {r['check']['detail']}"}
            for r in reasons if r["check"]["status"] in ("dead", "missing", "weak")]
