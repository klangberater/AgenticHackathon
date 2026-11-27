"""PDF report of a run: python -m taste report OUT_DIR

One document for the whole run (report.pdf): an overview, then for every product the rating, what decided
it, every flag with its quote and source link, the facts the script collected, and what could not be
checked. It ends with the method and the rubric the agent was given, so a reader sees the assumptions.
The HTML it is printed from (report.html) is kept; open that if no browser is available for printing.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import subprocess
import sys
from pathlib import Path

from . import agent, browser, cli

LABEL = {"green": "Green", "yellow": "Yellow", "red": "Red"}
MEANING = {"green": "shortlist it", "yellow": "shortlist only after resolving the yellow flags",
           "red": "do not shortlist"}
CHECK = {"ok": "quote confirmed on the page", "weak": "quote only partly matched",
         "unreadable": "page could not be re-read by the checker"}

CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body { font: 10pt/1.45 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #1c1c1a; margin: 0; }
h1 { font-size: 20pt; line-height: 1.15; margin: 0 0 2mm; letter-spacing: -0.01em; }
h2 { font-size: 14pt; line-height: 1.2; margin: 0 0 1mm; }
h3 { font-size: 10.5pt; margin: 6mm 0 2mm; }
p { margin: 0 0 2mm; }
a { color: #1a4fa0; word-break: break-all; }
.muted { color: #5c5c57; }
.small { font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: 1.6mm 2mm; border-bottom: 0.3pt solid #cfcec8; }
th { font-weight: 600; font-size: 8.5pt; color: #5c5c57; }
.chip { display: inline-block; padding: 0.3mm 2.2mm; border-radius: 1.2mm; font-weight: 700; font-size: 9pt; }
.green { background: #dff3e4; color: #14602c; } .yellow { background: #f7ecb5; color: #6a5200; }
.red { background: #fbe0dc; color: #9a1c12; }
.product { break-before: page; }
.head { display: grid; grid-template-columns: 34mm 1fr; gap: 5mm; border: 0.3pt solid #cfcec8; border-radius: 2mm; overflow: hidden; }
.call { display: grid; align-content: center; justify-items: center; padding: 5mm 2mm; font-size: 22pt; font-weight: 700; }
.call small { font-size: 7.5pt; font-weight: 500; text-align: center; line-height: 1.25; margin-top: 1mm; }
.ident { padding: 4mm 4mm 3mm 0; }
.readings { display: grid; grid-template-columns: repeat(4, 1fr); margin: 3mm 0; border: 0.3pt solid #cfcec8; border-radius: 2mm; }
.readings div { padding: 2mm 3mm; border-right: 0.3pt solid #cfcec8; } .readings div:last-child { border-right: 0; }
.readings b { display: block; font-size: 11pt; }
.flaggroup { margin-top: 4mm; }
.flaggroup > h3 { margin: 0; padding: 1.5mm 3mm; border-radius: 1.5mm 1.5mm 0 0; break-after: avoid; }
.flag { padding: 2.5mm 3mm; border: 0.3pt solid #cfcec8; border-top: 0; break-inside: avoid; }
.flag .claim { font-weight: 600; }
.flag blockquote { margin: 1.5mm 0; padding: 1mm 2.5mm; border-left: 1.5pt solid #b9b8b1; background: #f4f3ee; font-size: 9pt; }
.facts td:first-child { width: 42mm; color: #5c5c57; }
ul { margin: 0 0 2mm; padding-left: 5mm; } li { margin-bottom: 0.8mm; }
.rubric { white-space: pre-wrap; font-size: 8.5pt; line-height: 1.4; background: #f4f3ee; padding: 3mm; border-radius: 1.5mm; }
.cover { margin-bottom: 6mm; }
.nowrap { white-space: nowrap; }
"""

e = html.escape


def _link(url: str, target: str | None = None) -> str:
    if not url.startswith(("http://", "https://")):
        return e(url)
    return f'<a href="{e(target or url, quote=True)}">{e(url)}</a>'


def _facts(sig: dict) -> list[tuple[str, str]]:
    """The collected facts worth showing a reader, each as (label, value). Missing ones are skipped."""
    rows: list[tuple[str, str]] = []

    def add(label, value):
        if value not in (None, "", [], {}):
            rows.append((label, str(value)))

    reg, wb, hp = sig.get("domain_registration", {}), sig.get("wayback", {}), sig.get("homepage", {})
    mail, certs, sm = sig.get("mail_setup", {}), sig.get("certificates", {}), sig.get("sitemap", {})
    legal = sig.get("legal_entity", {})
    add("Domain", sig.get("domain"))
    add("Domain registered", (reg.get("registration") or "")[:10] or reg.get("note"))
    add("First archived capture", wb.get("first_capture"))
    add("Latest archived capture", wb.get("last_capture"))
    add("Earliest trace of the domain", sig.get("earliest_trace"))
    add("Homepage answered", f"HTTP {hp.get('status')}" + (", read after rendering in a browser" if hp.get("rendered_in_browser") else ""))
    add("Copyright years on the page", ", ".join(map(str, hp.get("copyright_years", []))))
    add("Built with (hints)", ", ".join(hp.get("tech_hints", [])))
    add("Company email", (mail.get("mail_provider") or ("mail servers present" if mail.get("mx") else "no mail servers"))
        + (", SPF" if mail.get("spf") else "") + (", DMARC" if mail.get("dmarc") else "") if "mx" in mail else None)
    add("First TLS certificate", certs.get("first_issued"))
    add("Product subdomains", ", ".join(certs.get("product_subdomains", [])))
    add("Legal entity on the site", ", ".join(legal.get("entity_names", [])))
    add("Register number", ", ".join(legal.get("register_numbers", [])) + (" " + legal.get("register_court", "") if legal.get("register_court") else ""))
    vat = legal.get("vat_check", {})
    add("VAT ID", f"{vat.get('vat_id')}: {'valid' if vat.get('valid') else 'not valid'} in the EU register" if vat.get("valid") is not None else None)
    add("Sitemap", f"{sm.get('entries')} pages, edited on {sm.get('distinct_lastmod_days')} different days, newest {sm.get('newest_lastmod')}" if sm.get("distinct_lastmod_days") else None)
    for a in sig.get("publishing_activity", []):
        if a.get("newest"):
            add(f"Newest dated {a['kind']} entry", f"{a['newest']} ({a['dated_entries']} dated entries found)")
    lc = sig.get("link_check", {})
    add("Internal links sampled", f"{lc.get('checked')} checked, {len(lc.get('dead', []))} dead" if lc.get("checked") else None)
    add("Pages not found from the homepage", ", ".join(sig.get("missing_key_pages", [])))
    add("Note", sig.get("blocked_note"))
    return rows


def _gist(v: dict, limit: int = 260) -> str:
    """The start of the summary for the overview, without an opening that only restates the rating."""
    text = v.get("summary") or v.get("error", "")
    parts = text.split(". ")
    while len(parts) > 1 and len(parts[0]) < 45:
        parts.pop(0)
    text = ". ".join(parts)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _product(v: dict, sig: dict) -> str:
    if "error" in v:
        return f'<section class="product"><h2>{e(v["url"])}</h2><p>The run failed: {e(v["error"])}</p></section>'
    c = v["verdict"]
    out = [f'<section class="product"><div class="head"><div class="call {c}">{LABEL[c]}<small>{e(MEANING[c])}</small></div>',
           f'<div class="ident"><h2>{e(v["product_name"])}</h2>',
           f'<p>{e(v.get("product_category", ""))}</p><p class="muted">{e(v["vendor_name"])}</p>']
    if v.get("former_names"):
        out.append(f'<p class="muted small">Formerly: {e(", ".join(v["former_names"]))}</p>')
    if v.get("identity"):
        out.append(f'<p class="small">{e(v["identity"])}</p>')
    out.append(f'<p class="small">{_link(v["url"])}</p></div></div>')
    out.append('<div class="readings">' + "".join(
        f'<div><span class="muted small">{label}</span><b>{e(str(val).replace("_", " "))}</b></div>' for label, val in (
            ("Product real and alive", v["product_real_and_alive"]), ("Company trustworthy", v["company_trustworthy"]),
            ("Lifecycle", v["lifecycle"]), ("Confidence", v["confidence"]))) + "</div>")
    out.append(f'<h3>Why this rating</h3><p>{e(v["summary"])}</p>')
    for colour in ("red", "yellow", "green"):
        fl = cli.flags(v, colour)
        if not fl:
            continue
        out.append(f'<div class="flaggroup"><h3 class="{colour}">{LABEL[colour]} flags ({len(fl)})</h3>')
        for r in fl:
            notes = [r["category"], "independent source" if r["independent"] else "vendor's own source"]
            if "confirmed" in r:
                notes.append("confirmed" if r["confirmed"] else "not confirmed")
            notes.append(CHECK.get(r["check"]["status"], r["check"]["status"]))
            out.append(f'<div class="flag"><p class="claim">{e(r["claim"])}</p><blockquote>“{e(r["quote"])}”</blockquote>'
                       f'<p class="small">{_link(r["source_url"], cli.evidence_link(r))}</p>'
                       f'<p class="small muted">{e("; ".join(notes))}</p></div>')
        out.append("</div>")
    if v.get("could_not_check"):
        out.append("<h3>What could not be checked</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in v["could_not_check"]) + "</ul>")
    if v.get("dropped_reasons"):
        out.append('<h3>Findings left out</h3><p class="small muted">Removed by the automatic checks because the source '
                   "did not load, did not contain the quote, or did not carry the claim.</p><ul class=\"small\">"
                   + "".join(f"<li>{e(r['claim'])} ({e(r['check'].get('detail') or r['check']['status'])}) {_link(r['source_url'])}</li>"
                             for r in v["dropped_reasons"]) + "</ul>")
    facts = _facts(sig)
    if facts or sig.get("flags_to_check") or sig.get("solid_signs"):
        out.append('<h3>Facts collected by script before the investigation</h3><table class="facts small">'
                   + "".join(f"<tr><td>{e(k)}</td><td>{e(val)}</td></tr>" for k, val in facts) + "</table>")
        for title, key in (("Cross-checks that raised a question", "flags_to_check"), ("Cross-checks that speak for the vendor", "solid_signs")):
            if sig.get(key):
                out.append(f'<p class="small" style="margin-top:2mm"><b>{title}</b></p><ul class="small">'
                           + "".join(f"<li>{e(x)}</li>" for x in sig[key]) + "</ul>")
    meta = v.get("_meta", {})
    out.append(f'<p class="small muted">Checked on {e(sig.get("checked_on", ""))} in {meta.get("wall_seconds", "?")} seconds'
               f'{"" if meta.get("screenshot", True) else ", without a screenshot"}.</p></section>')
    return "".join(out)


def _method() -> str:
    s = agent.SYSTEM
    rubric = s[s.index("The verdict answers one question"): s.index("How to investigate:")].strip()
    return f"""<section class="product"><h2>How these ratings were produced</h2>
<h3>Process</h3>
<ol>
<li><b>A script collects facts, without AI.</b> Domain registration and archive history, mail and certificate setup, VAT and register numbers on the legal page, dead links, dates on changelog and blog pages, the site's own claims about its age and size, and a screenshot. It cross-checks them, for example a large customer claim on a young domain.</li>
<li><b>An AI agent investigates like a buyer.</b> It receives those facts and the screenshot, then searches the web for what the vendor's site does not say: regulator actions, lawsuits, incidents, ownership, customers confirming from their side, signs of ongoing development, and what is normal among one or two direct competitors.</li>
<li><b>The agent rates the product</b> against the rubric below and files each finding as a green, yellow or red flag with a source link and a verbatim quote. The most serious confirmed ground sets the rating.</li>
<li><b>Code re-opens every source.</b> A finding is removed if its page does not load or does not contain the quote. A second, separate AI check then compares each claim with the passage around its quote and removes claims the passage does not carry.</li>
</ol>
<h3>Assumptions and limits</h3>
<ul>
<li>The rating answers whether an enterprise buyer should shortlist the product as it stands. It is a judgement for this purpose, not a public rating of the company.</li>
<li>The rating is an AI judgement guided by a written rubric, not a numeric score. A borderline product can come out one level different on a second run.</li>
<li>Looks are treated as a lead to follow up, never as the rating itself.</li>
<li>What a vendor says about itself is not counted as confirmation of its own claims; independent sources are preferred and marked.</li>
<li>Every finding is only as current as its source on the day of the check. Sources that block automated access are listed under what could not be checked.</li>
<li>No product was researched by hand; the report is the agent's output after the automatic checks.</li>
</ul>
<h3>The rubric given to the agent</h3>
<div class="rubric">{e(rubric)}</div></section>"""


def build(out: Path) -> Path:
    """Write report.html and report.pdf for the verdicts in `out`. Returns the PDF path (or the HTML if printing failed)."""
    verdicts = json.loads((out / "verdicts.json").read_text())
    sigs = {}
    for v in verdicts:
        f = out / f"{cli.slug(v['url'])}.signals.json"
        sigs[v["url"]] = json.loads(f.read_text()) if f.is_file() else {}
    rows = "".join(
        f'<tr><td>{i}</td><td><b>{e(v.get("product_name", v["url"]))}</b><br><span class="muted small">{e(v["url"])}</span></td>'
        f'<td>{"<span class=\'chip " + v["verdict"] + "\'>" + LABEL[v["verdict"]] + "</span>" if "verdict" in v else "failed"}</td>'
        f'<td class="small nowrap">{len(cli.flags(v, "red"))} red<br>{len(cli.flags(v, "yellow"))} yellow<br>{len(cli.flags(v, "green"))} green</td>'
        f'<td class="small">{e(_gist(v))}</td></tr>'
        for i, v in enumerate(verdicts, 1))
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Vendor check report</title><style>{CSS}</style></head><body>'
           f'<div class="cover"><h1>Vendor check report</h1><p class="muted">{len(verdicts)} product(s), '
           f'{dt.date.today().strftime("%d %B %Y")}. Green: shortlist. Yellow: shortlist only after resolving the '
           f'yellow flags. Red: do not shortlist. Every flag links to its source and quotes it.</p></div>'
           f'<table><tr><th></th><th>Product</th><th>Rating</th><th>Flags</th><th>What decided it</th></tr>{rows}</table>'
           + "".join(_product(v, sigs[v["url"]]) for v in verdicts) + _method() + "</body></html>")
    html_path, pdf_path = out / "report.html", out / "report.pdf"
    html_path.write_text(doc, encoding="utf-8")
    exe = browser.binary()
    if exe:
        pdf_path.unlink(missing_ok=True)
        try:
            subprocess.run([exe, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                            f"--print-to-pdf={pdf_path.resolve()}", html_path.resolve().as_uri()],
                           capture_output=True, timeout=120)
        except (subprocess.TimeoutExpired, OSError):
            pass
        if pdf_path.is_file() and pdf_path.stat().st_size > 2000:
            return pdf_path
    return html_path


if __name__ == "__main__":
    print(build(Path(sys.argv[1] if len(sys.argv) > 1 else "out")))
