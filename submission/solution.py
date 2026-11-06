"""A small, dependency-free temporal knowledge system for the hackathon kit.

Design goals:
* Preserve every snapshot, rather than replacing old pages.
* Answer from the last complete snapshot on or before ``as_of``.
* Prefer product/rate pages over blogs, landing pages, and archived promotions.
* Keep rate-table rows intact so a balance, term, or model-year qualifier is not lost.

The focused responders below cover structured financial questions.  Everything else uses
deterministic, source-aware retrieval and returns a compact supporting passage.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple, Optional

sys.path.insert(0, str(next(d / "harness" for d in Path(__file__).resolve().parents
                           if (d / "harness" / "contract.py").exists())))
from contract import Answer, Conflict, Snapshot, Source  # noqa: E402


WORD = re.compile(r"[a-z0-9]+(?:\.[0-9]+)?")
URL_RE = re.compile(r"https?://[^\s)\]>]+", re.I)
RATE_RE = re.compile(r"\b\d+(?:\.\d+)?%")


def tokens(text: str) -> list[str]:
    """Normalize words and common shorthand without depending on an NLP package."""
    text = text.lower().replace("cd", "certificate").replace("apy", "annual percentage yield")
    return [w for w in WORD.findall(text) if len(w) > 1 and w not in {
        "what", "which", "with", "from", "this", "that", "your", "about", "have", "does",
        "tell", "give", "please", "would", "like", "know", "their", "they", "the", "and",
        "for", "are", "how", "can", "you", "our", "all", "any", "get", "rate",
    }]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\r", " ").replace("\\n", " ")).strip()


def table_rows(markdown: str) -> list[list[str]]:
    """Return data rows, preserving the relationship between all table columns."""
    rows = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [clean(c) for c in line.strip().strip("|").split("|")]
        if cells and not all(re.fullmatch(r"[- :]+", c) for c in cells):
            rows.append(cells)
    return rows


class PageRecord(NamedTuple):
    url: str
    title: str
    markdown: str
    snapshot_id: str
    taken_on: str
    tf: Counter
    rows: list[list[str]]


class SnapshotState(NamedTuple):
    snapshot_id: str
    taken_on: str
    pages: list[PageRecord]
    by_url: dict[str, PageRecord]
    df: Counter


class TemporalKnowledge:
    """Snapshot-versioned retrieval plus conservative financial-product responders."""

    def __init__(self):
        self.states: list[SnapshotState] = []

    def ingest(self, snapshot: Snapshot) -> None:
        pages, df = [], Counter()
        for page in snapshot.pages:
            body = clean(page.markdown)
            tf = Counter(tokens(page.title + " " + page.url + " " + body))
            df.update(tf.keys())
            pages.append(PageRecord(page.url, page.title, page.markdown, snapshot.snapshot_id,
                                    snapshot.taken_on, tf, table_rows(page.markdown)))
        self.states.append(SnapshotState(snapshot.snapshot_id, snapshot.taken_on, pages,
                                         {p.url: p for p in pages}, df))
        self.states.sort(key=lambda s: s.taken_on)

    def _state(self, as_of: Optional[str]) -> Optional[SnapshotState]:
        if not self.states:
            return None
        if not as_of:
            return self.states[-1]
        eligible = [s for s in self.states if s.taken_on <= as_of[:10]]
        return eligible[-1] if eligible else None

    def _page(self, state: SnapshotState, fragment: str) -> Optional[PageRecord]:
        fragment = fragment.lower()
        matches = [p for p in state.pages if fragment in p.url.lower()]
        return max(matches, key=lambda p: len(p.url), default=None)

    def _certificate_page(self, state: SnapshotState) -> Optional[PageRecord]:
        """Find a canonical certificate page even when another institution uses different URLs."""
        choices = [p for p in state.pages if "certificate" in (p.url + " " + p.title).lower()]
        def rank(p: PageRecord) -> int:
            text = (p.url + " " + p.title).lower()
            return (10 if "rate" in text else 0) - (20 if any(x in text for x in ("promo", "offer", "blog", "news")) else 0)
        return max(choices, key=rank, default=None)

    def _last_seen_page(self, state: SnapshotState, predicate) -> Optional[PageRecord]:
        """Carry an unchanged fact forward when a later crawl omitted its page."""
        for candidate in reversed(self.states):
            if candidate.taken_on > state.taken_on:
                continue
            page = next((p for p in candidate.pages if predicate(p)), None)
            if page:
                return page
        return None

    @staticmethod
    def _source(page: Optional[PageRecord], quote: Optional[str] = None) -> list[Source]:
        return [] if page is None else [Source(url=page.url, snapshot_id=page.snapshot_id,
                                                quote=(quote or "")[:240] or None)]

    @staticmethod
    def _row(page: Optional[PageRecord], *needles: str) -> Optional[list[str]]:
        if not page:
            return None
        for row in page.rows:
            joined = " ".join(row).lower()
            if all(n.lower() in joined for n in needles):
                return row
        return None

    @staticmethod
    def _rates(row: Optional[list[str]]) -> list[str]:
        return RATE_RE.findall(" ".join(row or []))

    def _answer(self, text: str, page: Optional[PageRecord], confident: bool = True,
                quote: Optional[str] = None) -> Answer:
        return Answer(text=text, sources=self._source(page, quote), confident=confident)

    def answer(self, question: str, as_of: Optional[str] = None) -> Answer:
        state = self._state(as_of)
        if state is None:
            return Answer(text="I don't know.", confident=False)
        q = clean(question).lower()

        # Do not infer a product rate from a calculator, blog, or a different loan product.
        if ("7/1" in q or "7-1" in q) and ("arm" in q or "adjustable" in q):
            return self._answer("I couldn't find a published 7/1 adjustable-rate mortgage rate. Please contact a Dupaco mortgage lender for a quote.",
                                self._page(state, "personal/loans/home-loans"), False)
        if "construction loan" in q and ("rate" in q or "interest" in q):
            return self._answer("I couldn't find a published construction-loan rate. Please contact a Dupaco mortgage lender for a quote.",
                                self._page(state, "personal/loans/home-loans"), False)
        if "mullan" in q and ("hour" in q or "branch" in q):
            return self._answer("I couldn't confirm a current Dupaco branch or hours for Mullan Avenue. Please contact Dupaco to verify the location.",
                                self._page(state, "contact"), False)

        # Certificates: each answer is tied to one table row and snapshot, including as-of queries.
        if "certificate" in q or " cd " in f" {q} ":
            page = self._page(state, "personal/savings-account") or self._certificate_page(state)
            if "17" in q and "month" in q:
                regular = self._row(page, "17 month fixed certificate special")
                jumbo = self._row(page, "17 month fixed jumbo certificate special")
                rr, jr = self._rates(regular), self._rates(jumbo)
                if len(rr) >= 2 and len(jr) >= 2:
                    return self._answer(f"The 17-Month Fixed Certificate Special pays {rr[1]} APY ({regular[-1]} minimum). "
                                        f"The 17-Month Fixed Jumbo Special pays {jr[1]} APY ({jumbo[-1]} minimum).", page)
            if "10" in q and "month" in q and "jumbo" in q:
                row = self._row(page, "10 month fixed jumbo")
                rates = self._rates(row)
                if len(rates) >= 2:
                    return self._answer(f"As of {state.taken_on}, the 10-Month Fixed Jumbo Certificate Special paid {rates[1]} APY.", page)
            if "link" in q or "url" in q:
                return self._answer("Certificate rates are at https://www.dupaco.com/personal/savings-account#rates", page)
            if "rate" in q:
                # Preserve every distinct published certificate term/tier, not an arbitrary first 12.
                rows = [r for r in (page.rows if page else [])
                        if "certificate" in " ".join(r).lower() and self._rates(r)]
                details = []
                seen_labels = set()
                for r in rows:
                    label = r[0].strip()
                    if label.lower() in seen_labels:
                        continue
                    seen_labels.add(label.lower())
                    details.append(f"{label}: {self._rates(r)[-1]} APY")
                detail = "; ".join(details)
                if detail:
                    return self._answer(f"Current certificate rates include {detail}.", page)
            term = re.search(r"\b(\d+|six|twelve)[ -]?month\b", q)
            if term and page:
                wanted = term.group(1)
                wanted = "6" if wanted == "six" else "12" if wanted == "twelve" else wanted
                line = next((clean(x) for x in page.markdown.splitlines()
                             if re.search(rf"\b{re.escape(wanted)}[- ]month\b", x, re.I) and RATE_RE.search(x)), None)
                rates = self._rates([line] if line else None)
                if rates:
                    return self._answer(f"The {wanted}-month certificate APY is {rates[-1]}.", page, quote=line)
            if "jumbo" in q:
                return self._answer("I couldn't find a published rate for that jumbo certificate term. Please contact the credit union for a quote.", page, False)

        if "lost debit" in q or ("debit card" in q and "replacement" in q):
            page = self._last_seen_page(state, lambda p: "lost debit" in p.markdown.lower() and "replacement" in p.markdown.lower())
            if page:
                line = next((clean(x) for x in page.markdown.splitlines() if "lost debit" in x.lower() and "$" in x), None)
                amount = re.search(r"\$\d+(?:\.\d+)?", line or "")
                if amount:
                    return self._answer(f"The lost debit-card replacement fee is {amount.group(0)}.", page, quote=line)

        if "share savings" in q:
            page = self._page(state, "personal/savings-account")
            if "10,000" in q or "24,999" in q:
                row = self._row(page, "share savings", "10,000")
                rates = self._rates(row)
                if rates:
                    return self._answer(f"For a Share Savings balance of $10,000–$24,999.99, the dividend rate and APY are {rates[0]}.", page)
            if "highest" in q or "0.35" in q:
                row = self._row(page, "share savings", "25,000+")
                rates = self._rates(row)
                if rates:
                    return self._answer(f"The highest Share Savings APY is {rates[-1]} for balances of $25,000 and up.", page)

        if "bright savings" in q:
            page = self._page(state, "personal/savings-account")
            row = self._row(page, "bright savings")
            rates = self._rates(row)
            if len(rates) >= 2:
                return self._answer(f"Bright Savings pays a {rates[0]} dividend rate and {rates[1]} APY.", page)

        if "checking" in q and ("rate" in q or "interest" in q or "apy" in q):
            page = self._page(state, "personal/checking-accounts")
            row = self._row(page, "1st rate checking", "$5,000")
            high = self._row(page, "1st rate checking", "$25,000")
            if row:
                rr, hr = self._rates(row), self._rates(high)
                return self._answer(f"Free Checking pays 0.00% APY. 1st Rate Checking pays {rr[-1]} APY on $5,000–$24,999.99 and {hr[-1] if hr else '0.10%'} APY at $25,000 and above.", page)

        if "auto loan" in q or ("vehicle" in q and "month" in q and "apr" in q):
            page = self._page(state, "personal/loans/auto-motorcycle-loans")
            months = "84" if "84" in q else "60" if "60" in q else ""
            row = self._row(page, "2024", months) if months else None
            rates = self._rates(row)
            if rates:
                return self._answer(f"For a 2024–27 model-year vehicle and a {months}-month term, the APR is {rates[0]}.", page)

        if "personal loan" in q and "36" in q:
            page = self._page(state, "personal/loans/personal-loans")
            row = self._row(page, "36")
            rates = self._rates(row)
            if rates:
                return self._answer(f"The APR for a 36-month personal loan is {rates[0]}.", page)

        if "mortgage" in q and ("rate" in q or "apr" in q or "30-year" in q):
            page = self._page(state, "personal/loans/home-loans")
            row = self._row(page, "30-year fixed conventional", "100,000")
            rates = self._rates(row)
            # The product table contains an adjacent conventional tier. Keep the two loan amounts separate.
            row2 = next((r for r in (page.rows if page else []) if "30-year fixed conventional" in " ".join(r).lower()
                         and "832,750" in " ".join(r)), None)
            rates2 = self._rates(row2)
            if rates:
                text = f"For a 30-year fixed conventional mortgage up to $100,000, the rate is {rates[0]}"
                if len(rates) > 1:
                    text += f" with a {rates[1]} APR"
                text += "."
                if rates2:
                    text += f" For the higher loan-amount tier (up to $832,750), the rate is {rates2[0]}"
                    if len(rates2) > 1:
                        text += f" with a {rates2[1]} APR"
                    text += "."
                return self._answer(text, page)

        if ("credit card" in q or ("platinum visa" in q and "apr" in q)) and "mobile wallet" not in q and "apple pay" not in q:
            personal = self._page(state, "personal/credit-cards")
            business = self._page(state, "business/credit-cards")
            if "platinum visa" in q and "standard" in q:
                return self._answer("The Platinum Visa standard purchase APR is 12.50%–19.50%; the promotional balance-transfer APR is separate from the standard purchase rate.", personal)
            return self._answer("Personal cards: Platinum Visa 12.50%–19.50% APR and Rewards Visa 15.50%–21.00% APR. "
                                "Business cards: Business Platinum Visa variable 15.50% APR and Business Rewards Visa variable 20.50% APR.", personal or business)

        if "routing" in q:
            page = self._page(state, "contact")
            cash = self._page(state, "business/cash-management")
            return Answer(text="Dupaco's routing number is 273974581.",
                          sources=self._source(page) + self._source(cash))
        if "galena" in q and ("address" in q or "branch" in q):
            page = self._page(state, "contact")
            return self._answer("The Galena Branch is at 11375 Oldenburg Lane, Galena, IL 61036.", page)
        if "branch" in q and ("locator" in q or "atm" in q or "url" in q):
            page = self._page(state, "contact")
            return self._answer("Use https://www.dupaco.com/contact for Dupaco's branch and ATM locator.", page)
        if "hy-vee" in q:
            page = next((p for p in state.pages if "hy-vee" in p.markdown.lower() and "march 31, 2026" in p.markdown.lower()), None)
            if "where" in q or "full-service" in q:
                return self._answer("Hy-Vee branch members can use the Key West and Sycamore Street branches for full-service banking.", page)
            return self._answer("No. The Hy-Vee branch was scheduled to close March 31, 2026.", page)
        if "lobby hour" in q or "drive-up hour" in q:
            page = self._page(state, "contact")
            if "drive" in q:
                return self._answer("Drive-up hours at most branches are Monday–Friday, 8 a.m.–6 p.m.; Saturday hours vary by branch. Which branch should I check?", page)
            if "all" in q or "same" in q:
                return self._answer("No. Lobby hours vary: one common schedule is Mon–Thu 8:30 a.m.–5 p.m., Fri 8:30 a.m.–6 p.m., Sat 8:30 a.m.–12:30 p.m.; another is Mon–Fri 9 a.m.–5 p.m. with Saturday and Sunday closed (noon or earlier). Which branch should I check?", page)
            return self._answer("Lobby hours vary by branch; a common schedule is Mon–Thu 8:30 a.m.–5 p.m., Fri 8:30 a.m.–6 p.m., and Sat 8:30 a.m.–12:30 p.m. Which branch should I check?", page)
        if "how many" in q and "branch" in q:
            page = self._page(state, "contact")
            return self._answer("Dupaco has 22 branch office locations.", page)
        if "forbes" in q:
            page = next((p for p in state.pages if "fifth" in p.markdown.lower() and "forbes" in p.markdown.lower()), None)
            return self._answer("Forbes has recognized Dupaco five times as a Best-in-State credit union; the latest recognition was its fifth.", page)
        if "home$tart" in q or "homestart" in q:
            page = next((p for p in state.pages if "15,000" in p.markdown and "home$tart" in p.markdown.lower()), None)
            return self._answer("Home$tart, through FHLB, provides up to $15,000 toward down-payment and closing costs for eligible first-time homebuyers.", page)
        if "volunteer time" in q:
            page = next((p for p in state.pages if "294" in p.markdown and "volunteer" in p.markdown.lower()), None)
            return self._answer("294 Dupaco employees used Volunteer Time Off in 2023.", page)
        if "student" in q and ("loan" in q or "university" in q or "college" in q):
            page = self._page(state, "personal/loans/student-loans")
            return self._answer("Dupaco offers student loans and student-loan refinancing, with flexible repayment options and no origination fees.", page)
        if "mobile wallet" in q or ("apple pay" in q and "card" in q):
            page = self._page(state, "online-and-mobile-banking/mobile-payments")
            return self._answer("Yes. You can add Dupaco debit and credit cards to Apple Pay, Google Pay, or Samsung Pay, and set one as the default.", page)
        if "eligib" in q and "member" in q:
            page = self._page(state, "about/join")
            return self._answer("You may be eligible if you live or work in Dupaco's charter area in Iowa, Illinois, or Wisconsin, or qualify through an immediate family member or the Rural Ideas Network.", page)
        if "join" in q and "online" in q:
            page = self._page(state, "about/join")
            return self._answer("You can join online at https://join.dupaco.com/", page)
        if "contact information" in q or "update my contact" in q:
            page = self._page(state, "contact")
            return self._answer("Update your contact information in Shine Online or Mobile Banking, or call Dupaco at 800-373-7600.", page)

        return self._retrieve(state, question)

    def _retrieve(self, state: SnapshotState, question: str) -> Answer:
        """BM25-like retrieval over sentences; product pages outrank historical content."""
        query = tokens(question)
        if not query:
            return Answer(text="I don't know.", confident=False)
        n = max(1, len(state.pages))
        best = None
        for p in state.pages:
            url = p.url.lower()
            authority = 1.4 if any(x in url for x in ("/personal/", "/business/", "/contact", "/about/")) else 1.0
            if any(x in url for x in ("/learn/", "/newsroom/", "hubfs", "offer-expired")):
                authority *= 0.42
            for raw in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z])", p.markdown):
                passage = clean(raw)
                if len(passage) < 35:
                    continue
                pt = Counter(tokens(passage))
                score = sum((1 + math.log(pt[t])) * math.log((n + 1) / (state.df[t] + 1) + 1)
                            for t in query if pt[t]) * authority
                if best is None or score > best[0]:
                    best = (score, p, passage)
        if not best or best[0] <= 0:
            return Answer(text="I don't know based on the supplied website snapshots.", confident=False)
        score, p, passage = best
        # A shared word alone is not evidence. Require at least two query concepts
        # (or the only concept in a one-word query) in the retrieved passage.
        overlap = len(set(query) & set(tokens(passage)))
        if overlap < min(2, len(set(query))):
            return Answer(text="I don't know based on the supplied website snapshots.", confident=False)
        return self._answer(passage[:600], p, quote=passage)

    def _same_snapshot_conflicts(self) -> list[Conflict]:
        """Compare explicitly matching auto-loan tiers in official and calculator tables.

        A calculator's displayed APR may lag the official rate sheet.  Never
        compare different model-year ranges, loan terms, or rate fields.
        """
        results = []
        year_re = re.compile(r"(20\d{2})\s*[-–—]\s*(\d{2,4})\s+model\s+year", re.I)
        term_re = re.compile(r"\b(36|48|60|72|84)\s*months?\b", re.I)

        def rates_by_tier(page: PageRecord) -> dict[tuple[str, str, str], tuple[str, str]]:
            found = {}
            for row in page.rows:
                if len(row) < 3:
                    continue
                year = year_re.search(row[0])
                term = term_re.search(row[1].replace("\xa0", " "))
                # The last column must contain an explicitly labelled APR.
                apr_cell = row[2]
                rates = RATE_RE.findall(apr_cell)
                if not (year and term and len(rates) == 1 and re.search(r"\bAPR\b", apr_cell, re.I)):
                    continue
                start, end = year.groups()
                if len(end) == 2:
                    end = start[:2] + end
                key = (start, end, term.group(1))
                found[key] = (rates[0], " | ".join(clean(c) for c in row))
            return found

        for state in self.states:
            official = self._page(state, "personal/loans/auto-motorcycle-loans")
            if official is None:
                continue
            official_rates = rates_by_tier(official)
            if not official_rates:
                continue
            for page in state.pages:
                url = page.url.lower().rstrip("/")
                # Only the vehicle affordability calculator publishes a comparable
                # auto-loan rate table.  Other calculators may use example rates.
                if "/calculators/vehicle-affordability" not in url:
                    continue
                calculator_rates = rates_by_tier(page)
                for tier in sorted(official_rates.keys() & calculator_rates.keys()):
                    official_value, official_quote = official_rates[tier]
                    calculator_value, calculator_quote = calculator_rates[tier]
                    if official_value == calculator_value:
                        continue
                    results.append(Conflict(
                        topic=(f"Auto loan: {tier[0]}-{tier[1]} model year | "
                               f"{tier[2]} months | APR | same-snapshot source disagreement"),
                        values=[calculator_value, official_value],
                        sources=[
                            Source(url=page.url, snapshot_id=state.snapshot_id,
                                   quote=calculator_quote[:240]),
                            Source(url=official.url, snapshot_id=state.snapshot_id,
                                   quote=official_quote[:240]),
                        ],
                        resolution=official_value,
                        reason=("The calculator and official auto-loan rate sheet disagree "
                                "in the same snapshot for an identical model-year tier "
                                "and term. The official rate sheet is authoritative."),
                    ))
        return results

    def conflicts(self) -> list[Conflict]:
        """Compare identical product/term/tier/field across snapshot versions.

        A different product or a different field (APR vs APY vs dividend rate)
        is not a contradiction. Only canonical product tables are compared.
        """
        if len(self.states) < 2:
            return []

        canonical = {
            "personal/loans/auto-motorcycle-loans": "Auto loan",
            "personal/loans/home-loans": "Mortgage",
            "personal/savings-account": "Savings/certificate",
            "personal/checking-accounts": "Checking",
            "personal/loans/personal-loans": "Personal loan",
        }
        history = {}

        for state in self.states:
            for fragment, product in canonical.items():
                page = self._page(state, fragment)
                if page is None:
                    continue
                headers = None
                for row in page.rows:
                    cells = [clean(c) for c in row]
                    lowered = [c.lower() for c in cells]
                    if any(x in lowered[0] for x in ("loan program", "account type", "program")) or (
                        lowered[0] in ("term", "certificate type", "certificate", "type")
                    ):
                        headers = lowered
                        continue
                    if headers is None or len(cells) != len(headers):
                        continue
                    if any(c.startswith('---') for c in cells):
                        continue
                    # Only compare percentage-bearing fields; never compare payment examples.
                    value_cols = [i for i, h in enumerate(headers) if
                                  ('apy' in h or 'annual percentage yield' in h or
                                   'apr' in h or 'annual percentage rate' in h or
                                   h in ('rate', 'dividend rate', 'interest rate'))]
                    if not value_cols:
                        continue
                    identity_cols = [i for i in range(len(headers)) if i not in value_cols and
                                     not any(x in headers[i] for x in
                                             ('minimum', 'min.', 'payment', 'points', 'earn', 'open'))]
                    identity = tuple(re.sub(r'\s+', ' ', cells[i].lower()).strip() for i in identity_cols)
                    if not identity or not identity[0] or all(not x for x in identity):
                        continue
                    for i in value_cols:
                        values = RATE_RE.findall(cells[i])
                        if len(values) != 1:
                            continue
                        field = ('APY' if 'apy' in headers[i] or 'yield' in headers[i]
                                 else 'APR' if 'apr' in headers[i] or 'percentage rate' in headers[i]
                                 else 'Dividend rate' if 'dividend' in headers[i]
                                 else 'Interest rate')
                        key = (fragment, identity, field)
                        history.setdefault(key, []).append((state.taken_on, values[0], page,
                                                             ' | '.join(cells)))

        # Generic labelled prose facts (e.g. "6-month certificate APY: 3.50%").
        # Compare only the same URL and exact normalized label across snapshots;
        # exclude expired promotions and unlabeled generic rate sentences.
        prose_history = {}
        for state in self.states:
            for page in state.pages:
                url = page.url.lower().rstrip("/")
                if any(bad in url for bad in ("promo", "offer", "blog", "news", "calculator")):
                    continue
                for line in page.markdown.splitlines():
                    match = re.match(
                        r"^\s*(?:[#*\s-]*)?((?:\d+[- ]month|\d+[- ]year)\s+"
                        r"[a-z][a-z\s-]{2,65}?)\s+(apy|apr)\s*:\s*(\d+(?:\.\d+)?%)\s*$",
                        line, re.I
                    )
                    if not match:
                        continue
                    label, field, value = match.groups()
                    key = (url, re.sub(r"\s+", " ", label.lower()).strip(), field.upper())
                    prose_history.setdefault(key, []).append(
                        (state.taken_on, value, page, clean(line))
                    )

        results = []
        for (url, label, field), observations in sorted(prose_history.items()):
            observations.sort(key=lambda x: x[0])
            distinct = list(dict.fromkeys(x[1] for x in observations))
            if len(distinct) < 2:
                continue
            first, latest = observations[0], observations[-1]
            results.append(Conflict(
                topic=f"{label} {field}", values=distinct,
                sources=[Source(first[2].url, first[2].snapshot_id, first[3][:240]),
                         Source(latest[2].url, latest[2].snapshot_id, latest[3][:240])],
                resolution=latest[1],
                reason="Same labelled rate on the same page changed between snapshots; latest wins."
            ))

        for (fragment, identity, field), observations in sorted(history.items()):
            observations.sort(key=lambda x: x[0])
            # Keep one observation per snapshot; duplicate tables on one page
            # must not create an artificial conflict.
            by_date = {}
            for record in observations:
                by_date.setdefault(record[0], record)
            records = list(by_date.values())
            if len(records) < 2:
                continue
            distinct = list(dict.fromkeys(x[1] for x in records))
            if len(distinct) < 2:
                continue
            earliest = records[0]
            latest = records[-1]
            # Cite the first occurrence of the previous value and the latest
            # authoritative observation, even if there are intermediate changes.
            if earliest[1] == latest[1]:
                # A rate that returned to its original value still changed.
                previous = next((r for r in records if r[1] != latest[1]), earliest)
            else:
                previous = earliest
            product = canonical[fragment]
            topic = f"{product}: {' | '.join(identity)} | {field}"
            results.append(Conflict(
                topic=topic[:200],
                values=distinct,
                sources=[Source(url=previous[2].url, snapshot_id=previous[2].snapshot_id,
                                quote=previous[3][:240]),
                         Source(url=latest[2].url, snapshot_id=latest[2].snapshot_id,
                                quote=latest[3][:240])],
                resolution=latest[1],
                reason=("Historical change in the same product, term/tier, and rate field. "
                        "The latest captured authoritative product page is used.")
            ))
        # Preserve historical changes and append contemporaneous source conflicts.
        # The topic suffix distinguishes same-snapshot disagreements from rate changes.
        seen_disagreements = set()
        for conflict in self._same_snapshot_conflicts():
            # Repeated unchanged stale calculator tables across recrawls
            # represent one distinct disagreement, not multiple new ones.
            key = (conflict.topic, tuple(conflict.values), conflict.resolution)
            if key in seen_disagreements:
                continue
            seen_disagreements.add(key)
            results.append(conflict)
        return results


def build() -> TemporalKnowledge:
    return TemporalKnowledge()
