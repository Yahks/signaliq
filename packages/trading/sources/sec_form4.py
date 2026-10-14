# packages/trading/sources/sec_form4.py
"""SEC Form 4 XML parser.

Form 4 filings report insider transactions. Each filing's XML
gives: insider name, role, company, transaction date, transaction
code (S/P/M/A), shares, price, shares-after, and whether the
transaction was made under a Rule 10b5-1 plan.

The 10b5-1 flag is the analytic signal: a scheduled sale is not
the same as an opportunistic one.

Same free SEC API as sec_edgar.py. Same UA policy. Same Redis
cache pattern.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any

import httpx

from packages.core.logging import get_logger

log = get_logger(__name__)

SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_TIMEOUT = 15.0
USER_AGENT = "SignalIQ/0.1 (contact: ops@signaliq.local)"

# Transaction codes we care about:
#   P = open-market purchase
#   S = open-market sale
#   M = option exercise
#   A = grant/award
#   F = tax withholding
#   G = gift
BUY_CODES = {"P"}
SELL_CODES = {"S"}


def _text(elem, path: str) -> str | None:
    node = elem.find(path)
    if node is None:
        return None
    return (node.text or "").strip() or None


async def fetch_form4_transactions(
    cik: int,
    accession_no_hyphens: str,
    *,
    form4_filename: str = "form4.xml",
) -> list[dict[str, Any]]:
    """Fetch and parse one Form 4 filing's XML.

    `accession_no_hyphens` is the accession number without dashes,
    e.g. '000114036126038307'.

    Returns a list of transaction dicts. Never raises.
    """
    url = f"{SEC_ARCHIVES}/{cik}/{accession_no_hyphens}/{form4_filename}"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            if resp.status_code != 200:
                log.warning("form4.http_error", url=url, status=resp.status_code)
                return []
            xml_text = resp.text
    except Exception as exc:
        log.warning("form4.fetch_failed", url=url, error=str(exc)[:200])
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("form4.parse_failed", url=url, error=str(exc)[:200])
        return []

    # Filing-level metadata
    issuer_name = _text(root, "issuer/issuerName") or ""
    issuer_symbol = _text(root, "issuer/issuerTradingSymbol") or ""
    period = _text(root, "periodOfReport")
    owner_name = _text(root, "reportingOwner/reportingOwnerId/rptOwnerName") or ""
    is_officer = (_text(root, "reportingOwner/reportingOwnerRelationship/isOfficer") or "").lower() == "true"
    is_director = (_text(root, "reportingOwner/reportingOwnerRelationship/isDirector") or "").lower() == "true"
    is_ten_pct = (_text(root, "reportingOwner/reportingOwnerRelationship/isTenPercentOwner") or "").lower() == "true"
    officer_title = _text(root, "reportingOwner/reportingOwnerRelationship/officerTitle") or ""
    is_10b5_1 = (_text(root, "aff10b5One") or "").lower() == "true"

    if is_officer:
        role = officer_title or "Officer"
    elif is_director:
        role = "Director"
    elif is_ten_pct:
        role = "10% Owner"
    else:
        role = "Insider"

    # Footnote map — id → text
    footnotes: dict[str, str] = {}
    for fn in root.findall("footnotes/footnote"):
        fid = fn.attrib.get("id", "")
        text = "".join(fn.itertext()).strip()
        if fid and text:
            footnotes[fid] = text

    transactions: list[dict[str, Any]] = []
    for txn in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        code = _text(txn, "transactionCoding/transactionCode") or ""
        txn_date = _text(txn, "transactionDate/value")
        shares_s = _text(txn, "transactionAmounts/transactionShares/value")
        price_s = _text(txn, "transactionAmounts/transactionPricePerShare/value")
        ad_code = _text(txn, "transactionAmounts/transactionAcquiredDisposedCode/value")
        after_s = _text(txn, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")

        try:
            shares = float(shares_s) if shares_s else None
        except ValueError:
            shares = None
        try:
            price = float(price_s) if price_s else None
        except ValueError:
            price = None
        try:
            after = float(after_s) if after_s else None
        except ValueError:
            after = None

        CODE_LABELS = {
            "P": "bought (open-market purchase)",
            "S": "sold (open-market sale)",
            "M": "exercised options",
            "A": "received a grant of",
            "F": "had shares withheld for tax on",
            "G": "gifted",
            "C": "converted derivative securities (code C — NOT a sale)",
            "D": "disposed to the issuer",
            "J": "other acquisition/disposition",
            "X": "exercised in-the-money derivative",
        }
        action = CODE_LABELS.get(code, f"filed code {code}")

        transactions.append({
            "insider": owner_name,
            "role": role,
            "company": issuer_name,
            "symbol": issuer_symbol,
            "date": txn_date or period,
            "action": action,
            "code": code,
            "shares": shares,
            "price": price,
            "shares_after": after,
            "acquired_or_disposed": ad_code,
            "is_10b5_1": is_10b5_1,
            "footnotes": footnotes,
        })

    log.info(
        "form4.parsed",
        symbol=issuer_symbol,
        insider=owner_name,
        txns=len(transactions),
        has_10b5_1=is_10b5_1,
    )
    return transactions


async def fetch_recent_insider_trades(
    cik: int,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Fetch the last N Form 4 filings for a company and parse each.

    Uses the submissions API (in sec_edgar) to find the filing list,
    then fetches each Form 4's XML. Returns a flat list of
    transactions, newest first.
    """
    from .sec_edgar import _cik_for, _get_redis  # reuse helpers
    import json as _json
    import httpx

    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    try:
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": USER_AGENT})
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("form4.submissions_failed", cik=cik, error=str(exc)[:200])
        return []

    recent = (data.get("filings") or {}).get("recent") or {}
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])
    dates = recent.get("filingDate", [])

    # Take the first N Form 4s
    picks = [
        (accessions[i], primary_docs[i], dates[i])
        for i in range(len(forms))
        if forms[i] == "4"
    ][:limit]

    out: list[dict[str, Any]] = []
    for accession, primary_doc, filing_date in picks:
        acc_no_hyphens = accession.replace("-", "")
        # primaryDocument is typically "xslF345X06/form4.xml" — the
        # SEC-rendered HTML view. The raw XML is at "form4.xml" in
        # the same accession directory, without the xsl prefix.
        # Strip any leading xsl*/ directory and always fetch form4.xml.
        from pathlib import PurePosixPath
        doc_name = PurePosixPath(primary_doc or "form4.xml").name
        if not doc_name.endswith(".xml"):
            doc_name = "form4.xml"
        txns = await fetch_form4_transactions(
            cik, acc_no_hyphens, form4_filename=doc_name,
        )
        if not txns:
            txns = await fetch_form4_transactions(cik, acc_no_hyphens)
        for t in txns:
            t["filing_date"] = filing_date
            t["accession"] = accession
        out.extend(txns)

    log.info("form4.recent_trades", cik=cik, filings=len(picks), txns=len(out))
    return out