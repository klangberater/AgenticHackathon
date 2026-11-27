"""Deterministic, key-free signals about a vendor site.

Everything here is cheap, polite (a few dozen requests, spaced out) and comes
with a URL a human can open. The LLM gets these as a head start so it spends
its own tool calls on the parts that need judgement.
"""
from __future__ import annotations

import datetime as dt
import html
import ipaddress
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

UA = "Mozilla/5.0 (compatible; vendor-check/0.1)"
TIMEOUT = 12
MAX_BYTES = 1_500_000
MAX_LINK_CHECKS = 25
DELAY = 0.25  # seconds between requests to the vendor's own site

TWO_LEVEL_TLDS = {"co.uk", "org.uk", "ac.uk", "com.au", "co.nz", "co.jp", "com.br", "co.in", "co.za", "com.tr"}

KEY_PAGES = {
    "docs": r"docs?\b|documentation|developer|api\b",
    "help": r"help|support|faq|knowledge|hilfe",
    "changelog": r"changelog|release|what'?s[- ]new|updates?\b|roadmap",
    "blog": r"blog|news|\bpress\b|presse|aktuelles",
    "status": r"status",
    "pricing": r"pricing|preise|plans",
    "customers": r"customers?|case[- ]stud|kunden|referenzen|success",
    "about": r"about|team|company|ueber-uns|über|unternehmen",
    "careers": r"careers?|jobs|karriere",
    "security": r"security|trust|compliance|sicherheit",
    "legal": r"imprint|impressum|legal|terms|agb",
    "privacy": r"privacy|datenschutz|gdpr|dsgvo",
    "login": r"login|signin|sign-in|signup|sign-up|register|app\.",
    "integrations": r"integrations?|marketplace|partners?",
    "subprocessors": r"sub-?processors?|unterauftrag|data-processing|dpa\b|avv\b",
}
SOCIAL = ("linkedin.com", "github.com", "twitter.com", "x.com", "youtube.com", "facebook.com",
          "g2.com", "capterra", "trustpilot", "apps.apple.com", "play.google.com", "crunchbase.com")


def is_public(url: str) -> bool:
    """True only if the host resolves to public internet addresses.

    The URL comes from whoever uses the tool, and cited sources come from the model, so neither may
    point this machine at its own network (loopback, private ranges, cloud metadata).
    """
    host = urllib.parse.urlparse(url).hostname
    if not host or urllib.parse.urlparse(url).scheme not in ("http", "https"):
        return False
    try:
        return all(ipaddress.ip_address(info[4][0]).is_global for info in socket.getaddrinfo(host, None))
    except (OSError, ValueError):
        return False


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public(newurl):
            raise urllib.error.URLError(f"redirect to a non-public address refused: {newurl[:100]}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_PublicRedirects)


def fetch(url: str, max_bytes: int = MAX_BYTES) -> dict:
    """GET a URL. Never raises; returns status 0 plus an error string on failure."""
    if not is_public(url):
        return {"status": 0, "url": url, "text": "", "error": "not a public internet address", "headers": {}}
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8",
                                               "Accept-Language": "en,de;q=0.8"})
    try:
        with _OPENER.open(req, timeout=TIMEOUT) as r:
            raw = r.read(max_bytes)
            charset = r.headers.get_content_charset() or "utf-8"
            return {"status": r.status, "url": r.geturl(), "text": raw.decode(charset, "replace"),
                    "headers": dict(r.headers)}
    except urllib.error.HTTPError as e:
        return {"status": e.code, "url": url, "text": "", "error": str(e), "headers": dict(e.headers or {})}
    except Exception as e:  # DNS, TLS, timeout, ...
        return {"status": 0, "url": url, "text": "", "error": f"{type(e).__name__}: {e}", "headers": {}}


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self.title = ""
        self._href = None
        self._text: list[str] = []
        self._in_title = False
        self._skip = 0
        self.body: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "img" and (a.get("alt") or a.get("src")):
            name = (a.get("src") or "").split("?")[0].rsplit("/", 1)[-1][:60]
            self.images.append(f"{(a.get('alt') or '').strip()[:80]} [{name}]".strip())
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif tag == "a" and a.get("href"):
            self._href, self._text = a["href"], []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name and a.get("content"):
                self.meta[name] = a["content"][:300]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join("".join(self._text).split())[:80]))
            self._href = None
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._href is not None:
            self._text.append(data)
        self.body.append(data)


def page_text(html_text: str) -> str:
    """Visible text of an HTML page, whitespace-collapsed."""
    p = _Links()
    try:
        p.feed(html_text)
    except Exception:
        return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(html_text)).split())
    return " ".join(" ".join(p.body).split())


def registrable(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url):
        url = "https://" + url
    return url


def rdap(domain: str) -> dict:
    r = fetch(f"https://rdap.org/domain/{domain}", 300_000)
    out = {"source_url": f"https://rdap.org/domain/{domain}", "human_url": f"https://who.is/whois/{domain}"}
    if r["status"] != 200:
        return out | {"error": r.get("error") or f"HTTP {r['status']}"}
    try:
        d = json.loads(r["text"])
    except ValueError:
        return out | {"error": "not JSON"}
    for ev in d.get("events", []):
        out[ev.get("eventAction", "?").replace(" ", "_")] = ev.get("eventDate")
    for ent in d.get("entities", []):
        if "registrar" in ent.get("roles", []):
            for item in (ent.get("vcardArray") or [None, []])[1]:
                if item[0] == "fn":
                    out["registrar"] = item[3]
    if "registration" not in out:
        out["note"] = "registry does not publish a registration date (common for .de); use the Wayback first capture instead"
    return out


def wayback(domain: str) -> dict:
    base = "https://web.archive.org/cdx/search/cdx?output=json&fl=timestamp,statuscode&filter=statuscode:200"
    out = {"calendar_url": f"https://web.archive.org/web/*/{domain}"}
    r = fetch(f"{base}&url={domain}&collapse=timestamp:4&limit=40", 200_000)
    if r["status"] != 200:
        return out | {"error": r.get("error") or f"HTTP {r['status']}"}
    try:
        rows = json.loads(r["text"] or "[]")[1:]
    except ValueError:
        return out | {"error": "not JSON"}
    if not rows:
        return out | {"captures": 0, "note": "no archived captures of the homepage"}
    years = [row[0][:4] for row in rows]
    first = rows[0][0]
    out |= {"first_capture": f"{first[:4]}-{first[4:6]}-{first[6:8]}",
            "first_capture_url": f"https://web.archive.org/web/{first}/{domain}",
            "years_with_captures": years}
    # One snapshot per ~3 years so the agent can compare what the site claimed over time.
    out["snapshot_urls"] = [f"https://web.archive.org/web/{row[0]}/{domain}" for row in rows[::3]][-6:]
    # The model's fetch tool cannot open web.archive.org, so read the snapshots here and hand over excerpts.
    picks = [rows[0]] + ([rows[len(rows) // 2]] if len(rows) > 2 else [])
    out["snapshot_excerpts"] = []
    for row in picks:
        snap = f"https://web.archive.org/web/{row[0]}/{domain}"
        s = fetch(f"https://web.archive.org/web/{row[0]}id_/{domain}", 600_000)  # id_ = original page, no toolbar
        if s["status"] == 200:
            p = _Links()
            try:
                p.feed(s["text"])
            except Exception:
                continue
            body = " ".join(" ".join(p.body).split())
            out["snapshot_excerpts"].append({"url": snap, "date": f"{row[0][:4]}-{row[0][4:6]}-{row[0][6:8]}",
                                             "title": p.title.strip()[:200], "text_excerpt": body[:900]})
    last = fetch(f"{base}&url={domain}&limit=-1", 50_000)
    try:
        ts = json.loads(last["text"])[1][0]
        out["last_capture"] = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"
    except Exception:
        pass
    return out


def collect(url: str) -> dict:
    url = normalize(url)
    now = dt.date.today()
    home = fetch(url)
    final = home["url"]
    host = urllib.parse.urlparse(final).hostname or urllib.parse.urlparse(url).hostname or ""
    domain = registrable(host)
    out: dict = {"input_url": url, "final_url": final, "domain": domain, "checked_on": now.isoformat(),
                 "homepage": {"status": home["status"]}}
    if home.get("error"):
        out["homepage"]["error"] = home["error"]

    if home["status"] == 200 and len(page_text(home["text"])) < 1500:
        rendered = browser.rendered_html(final)  # a JavaScript app: read what a visitor's browser builds
        if len(page_text(rendered)) > len(page_text(home["text"])):
            home["text"] = rendered
            out["homepage"]["rendered_in_browser"] = True

    internal: dict[str, str] = {}
    text = ""
    hollow_social: list[str] = []
    if home["text"]:
        p = _Links()
        try:
            p.feed(home["text"])
        except Exception:
            pass
        text = " ".join(" ".join(p.body).split())
        # Take every year near a copyright mark, so "© 2005 - 2026" reads as up to 2026.
        years = sorted({int(y) for m in re.finditer(r"(?:©|&copy;|\(c\)|copyright)[^<>\n]{0,60}", home["text"], re.I)
                        for y in re.findall(r"\b(?:19|20)\d\d\b", m.group(0)) if int(y) <= now.year})
        hp = out["homepage"]
        hp |= {"title": p.title.strip()[:200], "description": p.meta.get("description", ""),
               "generator": p.meta.get("generator", ""), "visible_text_chars": len(text),
               "copyright_years": years, "server": home["headers"].get("Server", ""),
               "x_powered_by": home["headers"].get("X-Powered-By", "")}
        if years and max(years) < now.year - 1:
            hp["copyright_note"] = f"latest copyright year on the homepage is {max(years)}"
        if len(text) < 400:
            hp["note"] = "very little server-rendered text: likely a JavaScript app, fetch pages with a renderer"
        builders = re.findall(r"(webflow|framer|wix\.com|squarespace|wordpress|hubspot|lovable|v0\.dev|bolt\.new|"
                              r"next\.js|_next/|gatsby|shopify|carrd|durable\.co|typo3|drupal|joomla)", home["text"], re.I)
        hp["tech_hints"] = sorted({b.lower() for b in builders})

        out["page_text_excerpt"] = text[:7000]
        out["images_on_page"] = list(dict.fromkeys(p.images))[:70]  # alt text and file name: logo walls show up here
        external: dict[str, str] = {}
        outbound: dict[str, str] = {}
        key_pages: dict[str, list[str]] = {}
        for href, label in p.links:
            if href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absu = urllib.parse.urldefrag(urllib.parse.urljoin(final, href))[0]
            h = urllib.parse.urlparse(absu).hostname or ""
            if not absu.startswith("http"):
                continue
            if registrable(h) == domain:
                internal.setdefault(absu, label)
                hay = f"{urllib.parse.urlparse(absu).path} {h.split('.')[0]} {label}".lower()
                for kind, pat in KEY_PAGES.items():
                    if re.search(pat, hay) and absu not in key_pages.setdefault(kind, []):
                        if len(key_pages[kind]) < 3:
                            key_pages[kind].append(absu)
            else:
                outbound.setdefault(absu, label)
            if registrable(h) != domain and (any(s in h for s in SOCIAL) or "status" in h):
                external.setdefault(absu, label)
                if urllib.parse.urlparse(absu).path in ("", "/") and "status" not in h:
                    hollow_social.append(absu)
        out["key_pages"] = key_pages
        out["missing_key_pages"] = [k for k in ("docs", "help", "pricing", "customers", "about", "legal", "privacy")
                                    if k not in key_pages]
        out["external_profiles"] = list(external)[:25]
        out["outbound_links"] = [f"{label or '(no text)'} -> {u}" for u, label in list(outbound.items())[:50]]
        out["internal_link_count"] = len(internal)
        if hollow_social:
            out["hollow_social_links"] = sorted(set(hollow_social))

    # Dead-link check on a sample of the site's own links, key pages first.
    ordered = [u for us in out.get("key_pages", {}).values() for u in us]
    ordered += [u for u in internal if u not in ordered]
    dead, checked, blocked = [], 0, 0
    for u in ordered[:MAX_LINK_CHECKS]:
        if blocked >= 2:  # the site is rate-limiting or refusing us: stop asking
            break
        time.sleep(DELAY)
        r = fetch(u, 20_000)
        checked += 1
        blocked += r["status"] in (403, 429)
        if r["status"] in (0, 404, 410) or r["status"] >= 500:
            dead.append({"url": u, "status": r["status"], "error": r.get("error", "")[:120]})
    out["link_check"] = {"checked": checked, "dead": dead}
    if home["status"] in (403, 429) or blocked >= 2:
        out["blocked_note"] = ("the site refused this script's automated requests, so the homepage signals are "
                               "missing. That says nothing about the product; read its pages yourself.")

    root = f"{urllib.parse.urlparse(final).scheme or 'https'}://{host}"
    time.sleep(DELAY)
    in_domain = registrable(urllib.parse.urlparse(url).hostname or "")

    def history() -> None:
        out["domain_registration"] = rdap(domain)
        out["wayback"] = wayback(domain)
        if in_domain and in_domain != domain:  # redirected, e.g. after a rebrand: the old domain holds the history
            out["redirect_note"] = f"{in_domain} redirects to {domain}; history is given for both"
            out["input_domain_registration"] = rdap(in_domain)
            out["input_domain_wayback"] = wayback(in_domain)
        extra.offsite(out, domain)
        extra.flags(out, now)

    if home["status"] in (403, 429):  # refused: don't keep knocking
        history()
        return out
    extra.onsite(out, text, root, now)
    time.sleep(DELAY)
    st = fetch(root + "/.well-known/security.txt", 20_000)
    out["security_txt"] = st["status"] == 200 and "contact:" in st["text"].lower()
    history()
    return out


from . import browser, extra  # noqa: E402  (extra imports fetch and page_text from this module)

if __name__ == "__main__":
    import sys
    print(json.dumps(collect(sys.argv[1]), indent=2, ensure_ascii=False))
