"""Open every cited source and check that it says what the reason quotes.

The judges score evidence on exactly this, so no reason reaches the
submission file without having been re-opened by code, not by the model.
"""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor

from . import browser
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


def _context(quote: str, text: str, span: int = 1400) -> str:
    """The passage around the quote, for the check that the source really says what the claim says."""
    at = text.find(quote)
    if at < 0:
        words = quote.split()
        hits = [text.find(" ".join(words[i:i + 4])) for i in range(max(1, len(words) - 3))]
        at = min((h for h in hits if h >= 0), default=-1)
    if at < 0:
        return ""
    return text[max(0, at - span): at + len(quote) + span]


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
        return {"status": "ok", "detail": "", "context": _context(quote, text)}
    score = _overlap(quote, text)
    if score >= 0.6:
        return {"status": "ok", "detail": f"{score:.0%} of the quote matched", "context": _context(quote, text)}
    if len(text) < 1500:  # an empty shell: run its scripts and look again
        text = _norm(page_text(browser.rendered_html(url)))
        if quote in text or _overlap(quote, text) >= 0.6:
            return {"status": "ok", "detail": "found after rendering the page in a browser",
                    "context": _context(quote, text)}
        if len(text) < 1500:
            return {"status": "unreadable", "detail": "page renders client-side; could not read its text"}
        score = _overlap(quote, text)
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


def check_support(reasons: list[dict], judge) -> list[dict]:
    """Second opinion on each confirmed quote: does the passage actually say what the claim says?

    `judge` gets [{id, claim, quote, passage}] and returns [{id, support, why}]. Only a claim whose
    main fact is absent from the passage is returned for removal. Claims the passage partly carries
    are kept as written: the passage is a text excerpt, so details from elsewhere on the page or from
    its images would be cut wrongly (a trial run rewrote half of all claims and blunted real findings).
    Reasons whose source could not be re-read are left alone: nothing to judge them by.
    """
    items = [{"id": i, "claim": r["claim"], "quote": r["quote"], "passage": r["check"]["context"]}
             for i, r in enumerate(reasons) if r["check"].get("context")]
    removed = []
    if items:
        by_id = {a["id"]: a for a in judge(items)}
        for it in items:
            r, a = reasons[it["id"]], by_id.get(it["id"], {})
            r["check"]["support"] = a.get("support", "not_judged")
            if a.get("support") == "not_supported":
                r["check"] |= {"status": "unsupported", "detail": a.get("why", "the passage does not say this")}
                removed.append(r)
    for r in reasons:
        r["check"].pop("context", None)  # only needed for this check; keep result files small
    return removed
