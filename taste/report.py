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
CHECK = {"ok": "quote re-found on the page by code", "weak": "quote only partly re-found on the page",
         "unreadable": "the page blocks automated re-reading, so open the link to check"}

CSS = """
:root {
  --ink: #1c1c1a; --muted: #5c5c57; --rule: #d6d5cf; --wash: #f5f4ef; --link: #1a4fa0;
  --green-bg: #dff3e4; --green-ink: #14602c; --green-edge: #3f9a5c;
  --yellow-bg: #f7ecb5; --yellow-ink: #6a5200; --yellow-edge: #c9a400;
  --red-bg: #fbe0dc; --red-ink: #9a1c12; --red-edge: #d0493b;
  --sans: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
@page { size: A4; margin: 15mm 15mm 16mm; }
* { box-sizing: border-box; }
body { font: 10pt/1.45 var(--sans); color: var(--ink); background: #fff; margin: 0; }
h1 { font-size: 20pt; line-height: 1.15; margin: 0 0 2mm; letter-spacing: -0.01em; }
h2 { font-size: 15pt; line-height: 1.2; margin: 0 0 1mm; }
h3 { font-size: 11pt; margin: 6mm 0 2mm; break-after: avoid; }
p { margin: 0 0 2mm; }
a { color: var(--link); text-decoration: none; }
.muted { color: var(--muted); }
.small { font-size: 8.5pt; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; vertical-align: top; padding: 2mm; border-bottom: 0.3pt solid var(--rule); }
th { font-weight: 600; font-size: 8.5pt; color: var(--muted); }
tr { break-inside: avoid; }
ul, ol { margin: 0 0 2mm; padding-left: 5mm; } li { margin-bottom: 0.8mm; }
.chip { display: inline-block; padding: 0.3mm 2.2mm; border-radius: 1.2mm; font-weight: 700; font-size: 9pt; white-space: nowrap; }
.green { background: var(--green-bg); color: var(--green-ink); }
.yellow { background: var(--yellow-bg); color: var(--yellow-ink); }
.red { background: var(--red-bg); color: var(--red-ink); }
.howto { display: grid; grid-template-columns: repeat(3, 1fr); gap: 4mm; margin: 5mm 0 6mm; }
.howto div { background: var(--wash); border-radius: 1.5mm; padding: 3mm; font-size: 9pt; }
.howto b { display: block; margin-bottom: 1mm; }
.overview td.decided { font-size: 9.5pt; }
.product { break-before: page; }
.head { display: grid; grid-template-columns: 38mm 1fr; border: 0.3pt solid var(--rule); border-radius: 2mm; overflow: hidden; }
.call { display: grid; align-content: center; justify-items: center; padding: 5mm 2mm; font-size: 24pt; font-weight: 700; text-align: center; }
.call small { font-size: 8pt; font-weight: 500; line-height: 1.25; margin-top: 1mm; }
.ident { padding: 4mm 5mm 3mm; }
.ident p { margin-bottom: 1mm; }
.decided-by { margin: 5mm 0 0; padding: 3mm 4mm; border-left: 2.5pt solid var(--rule); background: var(--wash); font-size: 11.5pt; line-height: 1.4; break-inside: avoid; }
.edge-green { border-left-color: var(--green-edge); } .edge-yellow { border-left-color: var(--yellow-edge); }
.edge-red { border-left-color: var(--red-edge); }
.decided-by span { display: block; font-size: 8.5pt; color: var(--muted); margin-bottom: 0.8mm; }
.readings { display: grid; grid-template-columns: repeat(4, 1fr); margin: 4mm 0 0; border: 0.3pt solid var(--rule); border-radius: 2mm; }
.readings div { padding: 1.8mm 3mm; border-right: 0.3pt solid var(--rule); } .readings div:last-child { border-right: 0; }
.readings b { display: block; font-size: 10.5pt; }
.flag { display: grid; grid-template-columns: 9mm 1fr; gap: 0 2mm; padding: 2.5mm 0; border-bottom: 0.3pt solid var(--rule); break-inside: avoid; }
.flag:first-of-type { border-top: 0.3pt solid var(--rule); }
.flag .no { font-size: 8.5pt; padding: 0.3mm 1mm; text-align: center; align-self: start; }
.flag .claim { font-weight: 600; margin-bottom: 1.2mm; }
.flag blockquote { margin: 0 0 1.2mm; padding: 0.8mm 2.5mm; border-left: 1.5pt solid var(--rule); background: var(--wash); font-size: 9pt; }
.flag .src { font-size: 8.5pt; color: var(--muted); margin: 0; }
.flag .src a { word-break: break-all; }
.part { break-before: page; }
.part > h3:first-child { margin-top: 0; }
.facts td { padding: 1.2mm 2mm; } .facts td:first-child { width: 42mm; color: var(--muted); }
.rubric { white-space: pre-wrap; font-size: 8.5pt; line-height: 1.4; background: var(--wash); padding: 3mm; border-radius: 1.5mm; }
.nowrap { white-space: nowrap; }
.gaps { break-inside: avoid; }
.timeline { break-inside: avoid; }
.timeline ol { list-style: none; padding: 0; margin: 0; display: flex; flex-wrap: wrap; gap: 2mm; }
.timeline li { margin: 0; padding: 1.5mm 2.5mm; background: var(--wash); border-radius: 1.5mm; font-size: 8.5pt; color: var(--muted); }
.timeline li b { display: block; font-size: 10pt; color: var(--ink); }
.pair { display: grid; grid-template-columns: 1fr 1fr; border: 0.3pt solid var(--rule); border-radius: 2mm; margin-bottom: 3mm; break-inside: avoid; }
.pair > div { padding: 2.5mm 3mm; }
.pair > div + div { border-left: 0.3pt solid var(--rule); }
.pair .lab { font-size: 8.5pt; color: var(--muted); margin-bottom: 1mm; }
.pair .says { font-weight: 600; margin-bottom: 1.2mm; }
.pair blockquote { margin: 0 0 1.2mm; padding: 0.8mm 2.5mm; border-left: 1.5pt solid var(--rule); background: var(--wash); font-size: 9pt; }
.pair .src { font-size: 8.5pt; color: var(--muted); margin: 0; }
.change { margin: 3mm 0 0; padding: 2.5mm 3.5mm; border: 0.3pt solid var(--rule); border-radius: 2mm; break-inside: avoid; }
.howto p { margin: 0 0 1.2mm; }
.look { display: grid; grid-template-columns: 46mm 1fr; gap: 5mm; align-items: start; margin-top: 5mm; break-inside: avoid; }
.look:not(:has(img)) { grid-template-columns: 1fr; }
.look img { width: 46mm; height: 40mm; object-fit: cover; object-position: top; border: 0.3pt solid var(--rule); border-radius: 1.5mm; }
.look h3 { margin-top: 0; }
.design { break-inside: avoid; }
.shots { display: grid; grid-template-columns: 1fr 1fr; gap: 5mm; margin-top: 2mm; }
.shots figure { margin: 0; }
.shots img { width: 100%; height: 70mm; object-fit: cover; object-position: top; border: 0.3pt solid var(--rule); border-radius: 1.5mm; }
.shots figcaption { font-size: 8.5pt; color: var(--muted); margin-top: 1mm; }
"""

e = html.escape


def _short(url: str, n: int = 72) -> str:
    """The URL as a reader scans it: no scheme or www, cut to one line. The link itself stays complete."""
    u = url.split("://", 1)[-1].removeprefix("www.").rstrip("/")
    return u if len(u) <= n else u[: n - 1] + "…"


def _link(url: str, target: str | None = None, short: bool = False) -> str:
    if not url.startswith(("http://", "https://")):
        return e(url)
    return f'<a href="{e(target or url, quote=True)}">{e(_short(url) if short else url)}</a>'


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
    kev, repo = sig.get("exploited_vulnerabilities", {}), sig.get("code_repository", {})
    if "entries" in kev:
        add("CISA exploited-vulnerability catalogue", f"{kev['entries']} entries matching the vendor's name"
            + (f", latest added {kev['latest_added']}" if kev["entries"] else ""))
    if repo.get("stars") is not None:
        add("Code repository", f"{repo['repository']}: {repo['stars']} stars, last push {repo['last_push']}"
            + (f", top contributor made {repo['top_contributor_share']:.0%} of commits" if "top_contributor_share" in repo else "")
            + (", archived" if repo.get("archived") else ""))
    add("Russian-market signs on the page", "; ".join(sig.get("russian_market_signals", [])))
    for h in sig.get("help_and_docs", []):
        if h.get("checked"):
            add("Help or docs page", f"{h['url']}: {h['links_found']} links, {h['checked']} sampled, {len(h['dead'])} dead")
    add("Note", sig.get("blocked_note"))
    return rows


def _timeline(sig: dict) -> list[tuple[str, str]]:
    """Dated facts from the script's records, oldest first: when the domain, the archive and the activity began and last moved."""
    reg, wb, certs = sig.get("domain_registration", {}), sig.get("wayback", {}), sig.get("certificates", {})
    kev, repo, sm = sig.get("exploited_vulnerabilities", {}), sig.get("code_repository", {}), sig.get("sitemap", {})
    items = [(reg.get("registration"), "domain registered"), (wb.get("first_capture"), "first archived capture"),
             (certs.get("first_issued"), "first TLS certificate"), (wb.get("last_capture"), "latest archived capture"),
             (sm.get("newest_lastmod"), "newest sitemap edit"), (repo.get("last_push"), "latest code push"),
             (kev.get("latest_added") if kev.get("entries") else None, "latest CISA exploited-vulnerability entry")]
    items += [(a.get("newest"), f"newest {a['kind']} entry") for a in sig.get("publishing_activity", [])]
    years = sig.get("homepage", {}).get("copyright_years") or []
    if years:
        items.append((str(max(years)), "copyright year on the homepage"))
    out = sorted((str(d)[:10], label) for d, label in items if d and str(d)[:4].isdigit())
    return out


def _gist(v: dict, limit: int = 260) -> str:
    """The start of the summary for the overview, without an opening that only restates the rating."""
    text = v.get("summary") or v.get("error", "")
    parts = text.split(". ")
    while len(parts) > 1 and len(parts[0]) < 45:
        parts.pop(0)
    text = ". ".join(parts)
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " …"


def _deciding(v: dict) -> tuple[list[dict], bool]:
    """The flags that set the verdict, and whether the agent marked them itself.

    Runs from before the agent marked them (or a run where the marked flags were removed by the source checks)
    fall back to the flags of the verdict's colour, strongest first, which the rubric says set the verdict.
    """
    marked = [r for r in v.get("reasons", []) if r.get("decides") and r["flag"] == v["verdict"]]
    if marked:
        return marked, True
    return cli.flags(v, v["verdict"])[:3], False


def _numbers(v: dict) -> dict[int, str]:
    """A stable label per flag (R1, Y2, G3), so the page can refer to it."""
    seen: dict[str, int] = {}
    out = {}
    for r in v.get("reasons", []):
        seen[r["flag"]] = seen.get(r["flag"], 0) + 1
        out[id(r)] = f"{r['flag'][0].upper()}{seen[r['flag']]}"
    return out


HOLDS = {"yes": ("green", "The record bears it out"), "partly": ("yellow", "The record bears it out in part"),
         "no": ("red", "The record contradicts it")}


def _status(r: dict) -> str:
    status = CHECK.get(r["check"]["status"], r["check"]["status"])
    return status + ("; the link opens at the quote" if cli.evidence_link(r) != r["source_url"] else "")


def _pairs(v: dict) -> str:
    """The vendor's claims about itself next to the independent record that tests them."""
    rows = []
    for pr in v.get("site_vs_record", []):
        colour, label = HOLDS.get(pr["holds"], ("yellow", pr["holds"]))
        side = lambda x, head: (f'<div><p class="lab">{head}</p><p class="says">{e(x["says"])}</p>'
                                f'<blockquote>“{e(x["quote"])}”</blockquote>'
                                f'<p class="src">{_link(x["source_url"], cli.evidence_link(x), short=True)}<br>{e(_status(x))}.</p></div>')
        rows.append(f'<div class="pair">{side(pr["site"], "The vendor says")}'
                    + side(pr["record"], f'<span class="chip {colour}">{label}</span>') + "</div>")
    return ("<h3>What the vendor says, and what the record shows</h3>" + "".join(rows)) if rows else ""


def _flag(r: dict, no: str) -> str:
    src = "independent source" if r["independent"] else "the vendor's own page"
    status = _status(r)
    return (f'<div class="flag"><span class="chip no {r["flag"]}">{no}</span><div>'
            f'<p class="claim">{e(r["claim"])}</p><blockquote>“{e(r["quote"])}”</blockquote>'
            f'<p class="src">{_link(r["source_url"], cli.evidence_link(r), short=True)}<br>'
            f'From {src}; {status}.</p></div></div>')


def _product(v: dict, sig: dict, has_shot: bool = False, has_docs: bool = False) -> str:
    if "error" in v:
        return f'<section class="product"><h2>{e(v["url"])}</h2><p>The run failed: {e(v["error"])}</p></section>'
    c = v["verdict"]
    no = _numbers(v)
    deciding, marked = _deciding(v)
    out = [f'<section class="product"><div class="head"><div class="call {c}">{LABEL[c]}<small>{e(MEANING[c])}</small></div>',
           f'<div class="ident"><h2>{e(v["product_name"])}</h2>',
           f'<p>{e(v.get("product_category", ""))}</p><p class="muted">{e(v["vendor_name"])}</p>']
    if v.get("former_names"):
        out.append(f'<p class="muted small">Formerly: {e(", ".join(v["former_names"]))}</p>')
    out.append(f'<p class="small">{_link(v["url"])}</p></div></div>')
    if v.get("deciding_ground"):
        out.append(f'<div class="decided-by edge-{c}"><span>What decided the rating</span>{e(v["deciding_ground"])}</div>')
    if deciding:
        title = "The evidence that decided it" if marked else f"The strongest {LABEL[c].lower()} flags"
        out.append(f"<h3>{title}</h3>" + "".join(_flag(r, no[id(r)]) for r in deciding))
    out.append(_pairs(v))
    out.append(f'<h3>Our reasoning</h3><p>{e(v["summary"])}</p>')
    if v.get("would_change_call"):
        out.append(f'<div class="change"><b>What would change our call:</b> {e(v["would_change_call"])}</div>')
    out.append('<div class="readings">' + "".join(
        f'<div><span class="muted small">{label}</span><b>{e(str(val).replace("_", " "))}</b></div>' for label, val in (
            ("Product real and alive", v["product_real_and_alive"]), ("Company trustworthy", v["company_trustworthy"]),
            ("Lifecycle", v["lifecycle"]), ("Our confidence", v["confidence"]))) + "</div>")
    tl = _timeline(sig)
    if len(tl) >= 3:
        out.append('<div class="timeline"><h3>On the record</h3><ol>' + "".join(
            f'<li><b>{e(d[:7] if len(d) >= 7 else d)}</b>{e(label)}</li>' for d, label in tl) + "</ol></div>")
    d = v.get("design_read")
    shot = f"{cli.slug(v['url'])}.png"
    if d:
        out.append('<div class="look">' + (f'<img src="{e(shot, quote=True)}" alt="Screenshot of the top of the page">' if has_shot else "")
                   + f'<div><h3>How the site looks</h3><p><b>Design era:</b> {e(d["era"])}</p>'
                   f'<p>{e(d["bearing"])}</p></div></div>')
    if v.get("could_not_check"):
        out.append('<div class="gaps"><h3>What we could not verify</h3><p class="small muted">Not counted for or against '
                   "the vendor; a buyer would close these gaps before signing.</p><ul>"
                   + "".join(f"<li>{e(x)}</li>" for x in v["could_not_check"]) + "</ul></div>")
    rest = [r for r in v.get("reasons", []) if r not in deciding]
    if rest:
        counts = ", ".join(f"{len([r for r in rest if r['flag'] == col])} {col}" for col in ("red", "yellow", "green")
                           if any(r["flag"] == col for r in rest))
        out.append(f'<h3>All other flags ({counts})</h3>')
        for colour in ("red", "yellow", "green"):
            out += [_flag(r, no[id(r)]) for r in rest if r["flag"] == colour]
    if d:
        docs = f"{cli.slug(v['url'])}.docs.png"
        out.append('<div class="design"><h3>What we saw on the site</h3><ul>' + "".join(f"<li>{e(x)}</li>" for x in d["observations"])
                   + "</ul>" + ('<div class="shots">' + (f'<figure><img src="{e(shot, quote=True)}" alt=""><figcaption>Homepage</figcaption></figure>' if has_shot else "")
                   + (f'<figure><img src="{e(docs, quote=True)}" alt=""><figcaption>A help article from the same site, '
                      "to compare with the product the homepage shows</figcaption></figure>" if has_docs else "") + "</div>"
                      if has_shot or has_docs else "") + "</div>")
    if v.get("dropped_reasons"):
        out.append('<h3>Findings we removed</h3><p class="small muted">Taken out by the automatic checks because the source '
                   "did not load, did not contain the quote, or did not carry the claim.</p><ul class=\"small\">"
                   + "".join(f"<li>{e(r['claim'])} ({e(r['check'].get('detail') or r['check']['status'])}) {_link(r['source_url'], short=True)}</li>"
                             for r in v["dropped_reasons"]) + "</ul>")
    facts = _facts(sig)
    if facts or sig.get("flags_to_check") or sig.get("solid_signs"):
        out.append('<h3>Facts a script collected before the investigation</h3><table class="facts small">'
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
<li><b>A script collects facts, without AI.</b> Domain registration and archive history, mail and certificate setup, VAT and register numbers on the legal page, dead links, a sample of the help and docs articles, dates on changelog and blog pages, the site's own claims about its age and size, and two screenshots (the top of the page and the whole page). It cross-checks them, for example a large customer claim on a young domain.</li>
<li><b>An AI agent investigates like a buyer.</b> It receives those facts and the screenshot, then searches the web for what the vendor's site does not say: regulator actions, lawsuits, incidents, ownership, customers confirming from their side, signs of ongoing development, and what is normal among one or two direct competitors.</li>
<li><b>The agent rates the product</b> against the rubric below and describes what the page looks like from the screenshots, and files each finding as a green, yellow or red flag with a source link and a verbatim quote. The most serious confirmed ground sets the rating.</li>
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
        f'<tr><td>{i}</td><td><b>{e(v.get("product_name", v["url"]))}</b><br><span class="small">{_link(v["url"], short=True)}</span></td>'
        f'<td>{"<span class=\'chip " + v["verdict"] + "\'>" + LABEL[v["verdict"]] + "</span>" if "verdict" in v else "failed"}</td>'
        f'<td class="decided">{e(v.get("deciding_ground") or _gist(v))}</td>'
        f'<td class="small nowrap">{len(cli.flags(v, "red"))} red<br>{len(cli.flags(v, "yellow"))} yellow<br>{len(cli.flags(v, "green"))} green</td></tr>'
        for i, v in enumerate(verdicts, 1))
    cited = [r for v in verdicts for r in v.get("reasons", [])]
    found = sum(r["check"]["status"] == "ok" for r in cited)
    blocked = sum(r["check"]["status"] == "unreadable" for r in cited)
    removed = sum(len(v.get("dropped_reasons", [])) for v in verdicts)
    partly = len(cited) - found - blocked
    evidence = (f"{len(cited)} findings, each with a source link and a verbatim quote. Code re-opened every source and "
                f"re-found {found} of the quotes" + (f", {partly} in part" if partly else "")
                + (f"; {blocked} sit on pages that block automated reading" if blocked else "") + ". "
                + (f"{removed} other findings were removed because their source did not hold up." if removed else
                   "No finding had to be removed."))
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Vendor check report</title><style>{CSS}</style></head><body>'
           f'<h1>Vendor check report</h1><p class="muted">{len(verdicts)} product(s), checked on '
           f'{dt.date.today().strftime("%d %B %Y")}. The question for every product: would an enterprise procurement '
           f'team shortlist it as it stands?</p>'
           '<div class="howto"><div><b>The rating</b><p><span class="chip green">Green</span> shortlist it</p>'
           '<p><span class="chip yellow">Yellow</span> shortlist only after resolving the yellow flags</p>'
           '<p><span class="chip red">Red</span> do not shortlist</p></div>'
           '<div><b>One product per section</b>Each opens with the rating, the ground that decided it and the evidence '
           'for that ground; then our reasoning, what we could not verify, and every other flag.</div>'
           f'<div><b>The evidence</b>{e(evidence)} In a current browser each link opens the source scrolled to the '
           'quote, with the quote highlighted.</div></div>'
           f'<table class="overview"><tr><th></th><th>Product</th><th>Rating</th><th>What decided it</th><th>Flags</th></tr>{rows}</table>'
           + "".join(_product(v, sigs[v["url"]], (out / f"{cli.slug(v['url'])}.png").is_file(),
                             (out / f"{cli.slug(v['url'])}.docs.png").is_file()) for v in verdicts) + _method() + "</body></html>")
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
