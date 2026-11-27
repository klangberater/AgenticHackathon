"""Signals that are expensive to fake, plus cross-checks between them.

Off-site records (mail setup, certificate history, VAT register) exist only
because someone ran real infrastructure or a real company. The flags at the
end compare what the site claims with what those records show.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import re
import time

from .collect import DELAY, fetch, page_text

MONTHS = {m: i for i, names in enumerate([
    ("jan", "january", "januar"), ("feb", "february", "februar"), ("mar", "march", "märz", "maerz", "mär"),
    ("apr", "april"), ("may", "mai"), ("jun", "june", "juni"), ("jul", "july", "juli"), ("aug", "august"),
    ("sep", "sept", "september"), ("oct", "october", "okt", "oktober"), ("nov", "november"),
    ("dec", "december", "dez", "dezember")], 1) for m in names}
_MON = "|".join(sorted(MONTHS, key=len, reverse=True))
VAT = r"\b(ATU\d{8}|BE0?\d{9}|DE ?\d{9}|DK\d{8}|ES[A-Z0-9]\d{7}[A-Z0-9]|FI\d{8}|FR[A-Z0-9]{2} ?\d{9}|IE\d{7}[A-Z]{1,2}|" \
      r"IT\d{11}|LU\d{8}|NL\d{9}B\d{2}|PL\d{10}|PT\d{9}|SE\d{12}|CZ\d{8,10})\b"
APP_SUBDOMAINS = ("app", "login", "api", "status", "docs", "help", "support", "my", "portal", "cloud", "auth",
                  "developer", "developers", "community", "dashboard", "account", "id")


def dates_in(text: str, today: dt.date) -> list[dt.date]:
    """Every plausible calendar date written on a page (English and German formats), oldest first."""
    found = set()

    def add(y, m, d):
        try:
            x = dt.date(int(y), int(m), int(d))
        except ValueError:
            return
        if dt.date(2005, 1, 1) <= x <= today:
            found.add(x)

    for y, m, d in re.findall(r"\b(20\d\d)-(\d\d)-(\d\d)\b", text):
        add(y, m, d)
    for d, m, y in re.findall(r"\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b", text):
        add(y, m, d)
    for mon, d, y in re.findall(rf"\b({_MON})\.? (\d{{1,2}})(?:st|nd|rd|th)?,? (20\d\d)\b", text, re.I):
        add(y, MONTHS[mon.lower()], d)
    for d, mon, y in re.findall(rf"\b(\d{{1,2}})\.? ({_MON})\.?,? (20\d\d)\b", text, re.I):
        add(y, MONTHS[mon.lower()], d)
    return sorted(found)


def activity(url: str, today: dt.date) -> dict:
    """Dates on a changelog or blog page: is anyone still publishing, and how regularly?"""
    r = fetch(url)
    if r["status"] != 200:
        return {"url": url, "status": r["status"]}
    ds = dates_in(page_text(r["text"]), today)
    if not ds:
        return {"url": url, "dated_entries": 0, "note": "no dates found in the page text (may render client-side)"}
    year_ago = today - dt.timedelta(days=365)
    return {"url": url, "dated_entries": len(ds), "newest": ds[-1].isoformat(), "oldest": ds[0].isoformat(),
            "days_since_newest": (today - ds[-1]).days,
            "months_with_entries_last_12": len({(d.year, d.month) for d in ds if d >= year_ago})}


def sitemap(root: str) -> dict:
    url = root + "/sitemap.xml"
    r = fetch(url, 1_500_000)
    if r["status"] != 200 or "<" not in r["text"]:
        return {"url": url, "status": r["status"]}
    text = r["text"]
    if "<sitemapindex" in text:  # follow the first child that is likely to hold pages
        kids = re.findall(r"<loc>\s*([^<\s]+)", text)
        kid = next((k for k in kids if re.search(r"page|main|site", k, re.I)), kids[0] if kids else None)
        if kid:
            time.sleep(DELAY)
            text = fetch(kid, 1_500_000)["text"] or text
            url = kid
    mods = re.findall(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})", text)
    out = {"url": url, "entries": text.count("<loc>")}
    if mods:
        day, n = collections.Counter(mods).most_common(1)[0]
        out |= {"oldest_lastmod": min(mods), "newest_lastmod": max(mods), "distinct_lastmod_days": len(set(mods)),
                "most_common_day": day, "share_on_most_common_day": round(n / len(mods), 2)}
    return out


def dns(domain: str) -> dict:
    """Mail setup through Google's public DNS-over-HTTPS. A company that takes email has MX records."""
    def q(name, kind):
        r = fetch(f"https://dns.google/resolve?name={name}&type={kind}", 50_000)
        try:
            return [a["data"] for a in json.loads(r["text"]).get("Answer", [])]
        except Exception:
            return None
    mx = q(domain, "MX")
    if mx is None:
        return {"error": "lookup failed"}
    hosts = " ".join(mx).lower()
    provider = next((name for key, name in (("google", "Google Workspace"), ("outlook", "Microsoft 365"),
                                            ("zoho", "Zoho"), ("proton", "Proton"), ("mimecast", "Mimecast"),
                                            ("pphosted", "Proofpoint"), ("mailgun", "Mailgun"),
                                            ("registrar-servers", "registrar forwarding"),
                                            ("improvmx", "ImprovMX forwarding")) if key in hosts), "")
    txt = q(domain, "TXT") or []
    return {"source_url": f"https://dns.google/resolve?name={domain}&type=MX", "mx": mx[:5],
            "mail_provider": provider, "spf": any("v=spf1" in t for t in txt),
            "dmarc": bool(q(f"_dmarc.{domain}", "TXT")),
            "verification_records": sorted({t.split("=")[0].strip('"')[:40] for t in txt
                                            if "verification" in t.lower() or "verify" in t.lower()})[:12]}


def certs(domain: str) -> dict:
    """Certificate transparency: when the first TLS certificate was issued and which subdomains exist."""
    url = f"https://crt.sh/?q={domain}"
    r = fetch(url + "&output=json", 6_000_000)
    if r["status"] != 200:  # crt.sh is often overloaded; one retry is usually enough
        time.sleep(2)
        r = fetch(url + "&output=json", 6_000_000)
    try:
        rows = json.loads(r["text"])
    except Exception:
        return {"source_url": url, "error": r.get("error") or f"HTTP {r['status']} (crt.sh is often overloaded)"}
    if not rows:
        return {"source_url": url, "certificates": 0}
    names = {n.lstrip("*.").lower() for row in rows for n in row.get("name_value", "").split("\n")}
    subs = sorted(n[: -len(domain) - 1] for n in names if n.endswith("." + domain))
    return {"source_url": url, "certificates": len(rows),
            "first_issued": min(row["not_before"] for row in rows)[:10],
            "latest_issued": max(row["not_before"] for row in rows)[:10],
            "subdomain_count": len(subs), "subdomains": subs[:40],
            "product_subdomains": [s for s in subs if s.split(".")[0] in APP_SUBDOMAINS][:15]}


def vies(vat: str) -> dict:
    vat = vat.replace(" ", "")
    url = f"https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{vat[:2]}/vat/{vat[2:]}"
    r = fetch(url, 50_000)
    try:
        d = json.loads(r["text"])
    except Exception:
        return {"vat_id": vat, "source_url": url, "error": r.get("error") or f"HTTP {r['status']}"}
    out = {"vat_id": vat, "source_url": url, "valid": d.get("isValid")}
    if d.get("name") and d["name"] != "---":  # Germany and Spain do not disclose the holder
        out |= {"registered_name": d["name"], "registered_address": " ".join((d.get("address") or "").split())}
    if d.get("userError") not in (None, "VALID", "INVALID"):
        out["note"] = d["userError"]
    return out


def legal_entity(url: str) -> dict:
    """Company identifiers on the imprint or legal page, with the VAT ID checked against the EU register."""
    r = fetch(url)
    if r["status"] != 200:
        return {"url": url, "status": r["status"]}
    text = page_text(r["text"])
    out: dict = {"url": url}
    regs = re.findall(r"\b(HR[AB] ?\d{2,7}(?: ?[A-Z]{1,2})?)\b", text)
    court = re.search(r"(Amtsgericht|Registergericht:?) ([A-ZÄÖÜ][\wäöüß.-]+(?:[ -][A-ZÄÖÜ][\wäöüß.-]+)?)", text)
    uk = re.findall(r"(?:company|registration|registered) (?:number|no\.?):? ?(\d{8}|[A-Z]{2}\d{6})", text, re.I)
    forms = re.findall(r"\b(?:[A-ZÄÖÜ0-9][\w&.'-]*\s){1,4}(?:GmbH(?: & Co\. KG)?|AG|SE|UG \(haftungsbeschränkt\)|Ltd\.?|Limited|"
                       r"Inc\.?|LLC|B\.V\.|S\.A\.S?\.?|s\.r\.o\.|Oy|AB|ApS|A/S)\b", text)
    if regs:
        out["register_numbers"] = sorted(set(regs))[:4]
    if court:
        out["register_court"] = court.group(0)
    if uk:
        out["uk_company_numbers"] = sorted(set(uk))[:3]
    if forms:
        out["entity_names"] = [f.strip(" ,.-") for f, _ in collections.Counter(forms).most_common(4)]
    vats = sorted({v.replace(" ", "") for v in re.findall(VAT, text)})
    if vats:
        out["vat_check"] = vies(vats[0])
    if len(out) == 1:
        out["note"] = "no register number, VAT ID or legal form found on this page"
    return out


def claims(text: str) -> dict:
    """What the homepage says about its own age and size, kept verbatim so it can be checked."""
    out = {}
    m = re.search(r"\b(?:since|founded in|established in|est\.|seit|gegründet(?: im Jahr)?)\s+((?:19|20)\d\d)\b", text, re.I)
    if m:
        out["founded"] = {"year": int(m.group(1)), "quote": text[max(0, m.start() - 60): m.end() + 40].strip()}
    m = re.search(r"\b(\d[\d.,]*\s?(?:k|m|million|mio\.?|tausend)?\+?)\s+(?:\w+\s){0,2}?"
                  r"(customers|companies|businesses|teams|users|organi[sz]ations|kunden|unternehmen|nutzer)\b", text, re.I)
    if m:
        out["scale"] = {"quote": text[max(0, m.start() - 40): m.end() + 40].strip()}
    return out


def help_centre(url: str, sample: int = 8) -> dict:
    """Open the help or docs page and try a sample of the articles it links to.

    A help centre that exists as a link but whose articles are missing is one of the cheapest things
    to fake, and one a buyer notices only after signing.
    """
    import urllib.parse
    from .collect import _Links
    r = fetch(url)
    if r["status"] != 200:
        return {"url": url, "status": r["status"]}
    p = _Links()
    try:
        p.feed(r["text"])
    except Exception:
        pass
    host, here = urllib.parse.urlparse(r["url"]).hostname, urllib.parse.urldefrag(r["url"])[0].rstrip("/")
    links = []
    for href in p.hrefs:
        u = urllib.parse.urldefrag(urllib.parse.urljoin(r["url"], href))[0]
        pu = urllib.parse.urlparse(u)
        if pu.hostname == host and u.rstrip("/") != here and len(pu.path) > 1 and u not in links \
                and not re.search(r"login|signin|signup|logout|\.(png|jpg|svg|css|js|pdf|zip)$", u, re.I):
            links.append(u)
    out = {"url": r["url"], "links_found": len(links)}
    if len(page_text(r["text"])) < 400 and not links:
        return out | {"note": "the page renders client-side; the script could not read its article list"}
    # Prefer links below the help page itself (its own articles) over the site's general navigation.
    own = [u for u in links if u.startswith(here + "/")]
    picked = (own or links)[:sample]
    out["article_links_under_this_page"] = len(own)
    dead = []
    for u in picked:
        time.sleep(DELAY)
        s = fetch(u, 20_000)
        if s["status"] in (403, 429):
            out["note"] = "the site refused further automated requests; the sample is incomplete"
            break
        if s["status"] in (0, 404, 410) or s["status"] >= 500:
            dead.append({"url": u, "status": s["status"]})
    return out | {"checked": len(picked), "dead": dead}


def onsite(out: dict, home_text: str, root: str, today: dt.date) -> None:
    """Follow-ups that cost the vendor's site at most four more requests."""
    out["homepage_claims"] = claims(home_text)
    pages = out.get("key_pages", {})
    acts = []
    for kind in ("changelog", "blog"):
        if pages.get(kind):
            time.sleep(DELAY)
            index = min(pages[kind], key=len)  # the shortest URL is most likely the index, not one article
            acts.append({"kind": kind} | activity(index, today))
    out["publishing_activity"] = acts
    helpful = [min(pages[k], key=len) for k in ("help", "docs") if pages.get(k)][:2]
    out["help_and_docs"] = []
    for u in dict.fromkeys(helpful):
        time.sleep(DELAY)
        out["help_and_docs"].append(help_centre(u))
    legal = next((u for u in pages.get("legal", []) if re.search(r"imprint|impressum|legal-notice|legal$", u, re.I)),
                 (pages.get("legal") or [None])[0])
    if legal:
        time.sleep(DELAY)
        out["legal_entity"] = legal_entity(legal)
    time.sleep(DELAY)
    out["sitemap"] = sitemap(root)


_KEV: list | None = None
KEV_FEED = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"


def exploited_vulnerabilities(domain: str) -> dict:
    """Entries for this vendor in CISA's catalogue of vulnerabilities known to be exploited in the wild.

    Matched on the domain's name against the catalogue's vendor and product fields, so it is a lead:
    a short or generic name can match another vendor, and the agent confirms on the catalogue page.
    """
    global _KEV
    name = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())
    page = f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={domain.split('.')[0]}"
    if len(name) < 4:
        return {}
    if _KEV is None:
        try:
            _KEV = json.loads(fetch(KEV_FEED, 8_000_000)["text"]).get("vulnerabilities", [])
        except Exception:
            _KEV = []
            return {"source_url": page, "error": "catalogue could not be loaded"}
    hits = [v for v in _KEV if name in re.sub(r"[^a-z0-9]", "", (v.get("vendorProject", "") + v.get("product", "")).lower())]
    if not hits:
        return {"source_url": page, "entries": 0}
    hits.sort(key=lambda v: v.get("dateAdded", ""), reverse=True)
    return {"source_url": page, "entries": len(hits), "latest_added": hits[0].get("dateAdded"),
            "used_in_ransomware": sum(v.get("knownRansomwareCampaignUse") == "Known" for v in hits),
            "examples": [{"cve": v["cveID"], "product": f"{v.get('vendorProject')} {v.get('product')}",
                          "added": v.get("dateAdded"), "name": v.get("vulnerabilityName", "")[:90]} for v in hits[:5]]}


def russia_signals(html_text: str, links: list[str]) -> list[str]:
    """Signs on the page as it is today that the vendor addresses the Russian market.

    A Russian language version alone is common and proves little; rouble prices, a .ru storefront or a
    Russian reseller say more. All of it is a lead for the agent to weigh, dated today, not a finding.
    """
    found = []
    if re.search(r'hreflang=["\']ru(?:-ru)?["\']', html_text, re.I):
        found.append("the page declares a Russian language version (hreflang=ru)")
    ru_links = sorted({u for u in links if re.search(r"https?://[^/]*\.ru(/|$)|/ru(-ru)?(/|$)", u, re.I)})
    if ru_links:
        found.append("links to Russian pages or .ru sites: " + ", ".join(ru_links[:4]))
    if re.search(r"₽|\bRUB\b|руб\.", html_text):
        found.append("rouble prices or the RUB currency appear on the page")
    if re.search(r"Русский|Россия|Москва", html_text):
        found.append("Russian-language text on the page (for example a language switch or a Russian office)")
    return found


def code_repository(links: list[str]) -> dict:
    """Who actually writes the code: activity and contributor share of the GitHub repository the site links to."""
    import os
    repo = next((m.group(1) for u in links if (m := re.match(r"https?://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?(?:[#?].*)?$", u))), None)
    org = next((m.group(1) for u in links if (m := re.match(r"https?://github\.com/([\w.-]+)/?$", u))), None)

    def api(path):
        r = fetch(f"https://api.github.com/{path}", 400_000)
        try:
            return json.loads(r["text"]) if r["status"] == 200 else None
        except ValueError:
            return None
    if not repo and org and org.lower() not in ("sponsors", "features", "about"):
        repos = api(f"users/{org}/repos?sort=pushed&per_page=30") or []
        repos = [r for r in repos if not r.get("fork")]
        if repos:
            repo = max(repos, key=lambda r: r.get("stargazers_count", 0))["full_name"]
    if not repo:
        return {}
    info = api(f"repos/{repo}")
    if not info:
        return {"repository": f"https://github.com/{repo}", "note": "GitHub's API did not answer (rate limit or private)"}
    out = {"repository": info["html_url"], "stars": info.get("stargazers_count"), "forks": info.get("forks_count"),
           "last_push": (info.get("pushed_at") or "")[:10], "archived": info.get("archived", False),
           "contributors_url": info["html_url"] + "/graphs/contributors"}
    people = api(f"repos/{repo}/contributors?per_page=30") or []
    counts = [p.get("contributions", 0) for p in people if p.get("type") != "Bot"]
    if counts and sum(counts):
        out |= {"contributors_listed": len(counts), "top_contributor_share": round(max(counts) / sum(counts), 2),
                "contributors_with_10_or_more_commits": sum(c >= 10 for c in counts)}
    return out


def offsite(out: dict, domain: str) -> None:
    out["mail_setup"] = dns(domain)
    out["certificates"] = certs(domain)
    out["exploited_vulnerabilities"] = exploited_vulnerabilities(domain)
    links = [x.split(" -> ")[-1] for x in out.get("outbound_links", [])] + out.get("external_profiles", [])
    repo = code_repository(links)
    if repo:
        out["code_repository"] = repo


def _year(s: str | None) -> int | None:
    return int(s[:4]) if s and s[:4].isdigit() else None


def flags(out: dict, today: dt.date) -> None:
    """Cross-checks. Each flag is a lead with the two records that disagree, not a verdict."""
    f: list[str] = []
    solid: list[str] = []
    reg = out.get("domain_registration", {}).get("registration")
    first_cap = out.get("wayback", {}).get("first_capture")
    first_cert = out.get("certificates", {}).get("first_issued")
    for d in ("input_domain_registration", "input_domain_wayback"):  # a rebrand keeps the older domain's history
        v = out.get(d, {})
        reg = min(filter(None, [reg, v.get("registration")]), default=reg)
        first_cap = min(filter(None, [first_cap, v.get("first_capture")]), default=first_cap)
    earliest = min(filter(None, [reg and reg[:10], first_cap, first_cert]), default=None)
    if earliest:
        age_days = (today - dt.date.fromisoformat(earliest)).days
        out["earliest_trace"] = earliest
        if age_days < 365:
            f.append(f"no trace of this domain before {earliest}: registration, archive and certificates all start "
                     f"within the last year")
        elif age_days > 365 * 8:
            solid.append(f"the domain has existed since at least {earliest} (confirm from the archived snapshots "
                         f"that it was this vendor's all along; domains get resold)")
    claim = out.get("homepage_claims", {})
    founded = claim.get("founded", {}).get("year")
    if founded and earliest and _year(earliest) - founded >= 3 and (today.year - _year(earliest)) < 5:
        f.append(f"site says founded {founded}, but the earliest trace of the domain is {earliest}")
    if claim.get("scale") and earliest and (today - dt.date.fromisoformat(earliest)).days < 540:
        f.append(f"scale claim on a young domain ({earliest}): \"{claim['scale']['quote']}\". Look for outside confirmation")
    years = out.get("homepage", {}).get("copyright_years") or []
    if years and max(years) < today.year - 1:
        f.append(f"homepage copyright stops at {max(years)}")
    sm = out.get("sitemap", {})
    if sm.get("share_on_most_common_day", 0) >= 0.8 and sm.get("entries", 0) >= 15:
        f.append(f"{sm['share_on_most_common_day']:.0%} of sitemap pages were last modified on {sm['most_common_day']}: "
                 f"either the whole site was published in one go or the build stamps every page (check which)")
    elif sm.get("distinct_lastmod_days", 0) >= 20 and sm.get("oldest_lastmod", "9") < str(today.year - 2):
        solid.append(f"sitemap pages were edited on {sm['distinct_lastmod_days']} different days since {sm['oldest_lastmod']}")
    mail = out.get("mail_setup", {})
    if "mx" in mail and not mail["mx"]:
        f.append("the domain has no mail servers (no MX records), so nobody can email this company at its own domain")
    elif "forwarding" in mail.get("mail_provider", ""):
        f.append(f"company email is only {mail['mail_provider']}, not a hosted mailbox")
    elif mail.get("mx") and mail.get("dmarc") and mail.get("spf"):
        solid.append("mail is properly set up (MX, SPF and DMARC)")
    certs_ = out.get("certificates", {})
    if certs_.get("product_subdomains"):
        solid.append("product infrastructure has its own subdomains: " + ", ".join(certs_["product_subdomains"][:6]))
    elif certs_.get("certificates") and certs_.get("subdomain_count", 0) <= 1:
        f.append("certificate history shows no subdomain besides www: no sign of an app, API, docs or status host "
                 "on this domain (the product may live on another domain)")
    for a in out.get("publishing_activity", []):
        if a.get("days_since_newest") is None or a["dated_entries"] < 3:
            continue
        if a["days_since_newest"] > 365:
            f.append(f"newest dated entry on the {a['kind']} page is {a['newest']} ({a['days_since_newest']} days ago)")
        elif a["kind"] == "changelog" and a.get("months_with_entries_last_12", 0) >= 6:
            solid.append(f"changelog has entries in {a['months_with_entries_last_12']} of the last 12 months")
    vat = out.get("legal_entity", {}).get("vat_check", {})
    if vat.get("valid") is False:
        f.append(f"the VAT ID on the legal page ({vat['vat_id']}) is not valid in the EU register")
    elif vat.get("valid"):
        solid.append(f"VAT ID {vat['vat_id']} is valid in the EU register")
    if out.get("legal_entity", {}).get("note"):
        f.append("the legal page names no register number, VAT ID or legal form")
    for h in out.get("help_and_docs", []):
        if h.get("checked") and len(h.get("dead", [])) >= 2:
            f.append(f"{len(h['dead'])} of {h['checked']} sampled links on the help or docs page {h['url']} are dead: "
                     + ", ".join(d["url"] for d in h["dead"][:3]))
        elif h.get("checked") and h.get("article_links_under_this_page", 0) >= 20 and not h.get("dead"):
            solid.append(f"the help or docs page {h['url']} links to {h['article_links_under_this_page']} pages of its own "
                         f"and all {h['checked']} sampled ones load")
    kev = out.get("exploited_vulnerabilities", {})
    if kev.get("entries"):
        f.append(f"CISA lists {kev['entries']} vulnerabilities matching this vendor's name as exploited in the wild, the "
                 f"latest added {kev['latest_added']}" + (f", {kev['used_in_ransomware']} used in ransomware" if kev["used_in_ransomware"] else "")
                 + f". Confirm they are this vendor's products and how they were handled: {kev['source_url']}")
    if out.get("russian_market_signals"):
        f.append("signs on today's page that the vendor addresses the Russian market (check whether it still sells there): "
                 + "; ".join(out["russian_market_signals"]))
    repo = out.get("code_repository", {})
    if repo.get("archived"):
        f.append(f"the linked code repository is archived: {repo['repository']}")
    elif repo.get("last_push") and (today - dt.date.fromisoformat(repo["last_push"])).days > 365:
        f.append(f"the linked code repository was last pushed to on {repo['last_push']}: {repo['repository']}")
    if repo.get("top_contributor_share", 0) >= 0.8:
        f.append(f"one person made {repo['top_contributor_share']:.0%} of the commits among the top contributors of "
                 f"{repo['repository']} ({repo['contributors_with_10_or_more_commits']} people have 10 or more commits): "
                 f"{repo['contributors_url']}")
    elif repo.get("contributors_with_10_or_more_commits", 0) >= 8:
        solid.append(f"{repo['contributors_with_10_or_more_commits']} people have 10 or more commits in {repo['repository']}, "
                     f"last push {repo['last_push']}")
    if out.get("hollow_social_links"):
        f.append("social icons link to the platform's front page, not to a profile: " + ", ".join(out["hollow_social_links"][:4]))
    dead = out.get("link_check", {})
    if dead.get("checked") and len(dead.get("dead", [])) / dead["checked"] >= 0.2:
        f.append(f"{len(dead['dead'])} of {dead['checked']} sampled internal links are dead")
    out["flags_to_check"] = f
    out["solid_signs"] = solid
