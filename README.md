# Agents with Taste: a vendor check agent

**The verdicts on the ten live products: read [`stackgini-verdicts.pdf`](stackgini-verdicts.pdf) first.**
[`stackgini-verdicts.csv`](stackgini-verdicts.csv) holds the same verdicts in the labelled set's columns.
Green: shortlist it. Yellow: shortlist only after resolving the yellow flags. Red: do not shortlist.

Give it the website of a software product; it returns Green, Yellow or Red for one question: would an
enterprise procurement team shortlist this product as it stands? Every reason comes with a source link and a
verbatim quote from that source, and code re-opens every source before a reason reaches the report.

## The verdicts for the live round

**Start with [`stackgini-verdicts.pdf`](stackgini-verdicts.pdf)**: it holds the same verdicts as the CSV, laid out
so a reader sees each call, what decided it and the evidence for it in a page or two. The CSV is the requested
machine-readable format.

- **`stackgini-verdicts.csv`**: one row per product, in the columns of the labelled set.
  - `id`, `url`, `product`, `vendor`: as given to us.
  - `overall`: Green (shortlist it), Yellow (shortlist only after resolving the yellow flags), Red (do not shortlist).
  - `green_flags`, `yellow_flags`, `red_flags`: one reason per line, as `- claim Source says: "verbatim quote" link`.
    The link opens the source scrolled to the quote where the browser supports text fragments.
  - `reasoning`: what decided the call, the agent's reasoning, and what would change the call.
- **`stackgini-verdicts.pdf`**: the same verdicts as a report. Each product opens with the rating, the ground that
  decided it and its evidence, the vendor's claims set against independent records, what we could not verify, and
  a timeline of dated public records; then every other flag, the design read with screenshots, and the facts the
  script collected. The last section gives the method and the full rubric the agent was given.

## How it works

1. **A script collects hard-to-fake facts, without AI** (`taste/collect.py`, `taste/extra.py`, `taste/browser.py`):
   domain age, Wayback history, mail and certificate setup, VAT and register numbers, dead links, a sample of the
   help-centre articles, CISA exploited-vulnerability entries, code-repository health, Russia signals, screenshots
   of the homepage and of one help article. It cross-checks them into questions to follow up.
2. **An agent investigates like a buyer** (`taste/agent.py`): Claude Code in headless mode with only web search
   and web fetch. It reads the screenshots for design age, borrowed logos and mock-ups, searches for regulator
   actions, lawsuits, breaches, ownership and customers who confirm, reads review sites where they fit, and uses
   a competitor or two as a yardstick. The rubric is in the system prompt.
3. **Code checks the evidence** (`taste/verify.py`): every cited page is re-opened and the quote looked up; a second,
   tool-less model call removes claims the quoted passage does not carry. Reasons that fail are dropped and listed.
4. **The report** (`taste/report.py`) prints the PDF from HTML with headless Chrome.

## Run it

Needs Python 3.12+, the `claude` CLI logged in, and Chrome or a Playwright Chromium for screenshots and the PDF.

```
python3 -m taste run https://example.com -o out/one          # one product
python3 -m taste run -f products.csv -o out/live -p 5          # a CSV with a url column (id, product, vendor kept)
python3 -m taste run -f products.csv -o out/live --only example  # re-run some rows; the others keep their result
python3 -m taste report out/live                                # rebuild the PDF
```

Each run writes `report.pdf`, `verdicts.csv`, `verdicts.json` and `verdicts.md` to the output folder.
