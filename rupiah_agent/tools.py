"""The agent's tools. The same functions are served over MCP by mcp_server.py.

Every tool returns plain JSON with its source and the date of the data, so the
model can quote both, and the evals can check numbers against the same values.
"""
import difflib
import re

from bs4 import BeautifulSoup

from . import docs
from .data import fetch

FUNDS_URL = "https://pasardana.id/api/FundSearchResult/GetAll"
STOCKS_URL = "https://pasardana.id/api/StockSearchResult/GetAll"
BI_URL = "https://www.bi.go.id/en/statistik/indikator/bi-rate.aspx"
FX_URL = "https://api.frankfurter.app/latest?from=USD&to=IDR"

CATEGORY = {  # English and Indonesian names -> Pasardana category
    "money market": "Pasar Uang", "pasar uang": "Pasar Uang",
    "fixed income": "Pendapatan Tetap", "bond": "Pendapatan Tetap", "pendapatan tetap": "Pendapatan Tetap",
    "equity": "Saham", "stock": "Saham", "saham": "Saham",
    "balanced": "Campuran", "mixed": "Campuran", "campuran": "Campuran",
    "index": "Indeks", "indeks": "Indeks", "protected": "Terproteksi", "terproteksi": "Terproteksi",
    "global": "Reksadana Global", "etf": "Exchange Traded Fund",
}
# Pasardana's bulk feed has no expense ratio, Sharpe, drawdown or 3/5-year returns (all null, checked 2026-11-18),
# so only fields that are filled are offered.
SORT = {"return_1y": "YearlyReturn", "return_ytd": "YtdReturn", "return_6m": "SemiAnnualReturn",
        "return_3m": "QuarterlyReturn", "aum": "AssetUnderManagement"}


def pct(x):
    return None if x is None else round(x * 100, 2)


def _funds():
    rows, stale = fetch(FUNDS_URL)
    return [r for r in rows if r.get("InvestmentManagerName")], stale


def _fund_view(f: dict, stale: bool) -> dict:
    return {
        "name": f["Name"], "manager": f["InvestmentManagerName"], "category": f.get("ConservativeCategory"),
        "sharia": bool(f.get("Sharia")), "currency": f.get("Currency") or "IDR",
        "nav": f.get("NetAssetValue"), "nav_date": (f.get("LastUpdate") or "")[:10],
        "return_1m_pct": pct(f.get("MonthlyReturn")), "return_3m_pct": pct(f.get("QuarterlyReturn")),
        "return_6m_pct": pct(f.get("SemiAnnualReturn")), "return_ytd_pct": pct(f.get("YtdReturn")),
        "return_1y_pct": pct(f.get("YearlyReturn")),
        "aum_idr": f.get("AssetUnderManagement"), "aum_date": (f.get("AumPublishedDate") or "")[:10],
        "manager_fee": f.get("InvestmentManagerFee"), "min_subscription": f.get("MinimumSubscription"),
        "risk_classification": f.get("RiskClassificationValue"), "launch_date": (f.get("IpoDate") or "")[:10],
        "custodian": f.get("CustodianBankName"), "source": "pasardana.id", "stale": stale,
    }


def search_funds(query: str = "", category: str = "", sharia: bool | None = None,
                 sort_by: str = "return_1y", limit: int = 5, min_aum_idr: float = 0) -> dict:
    """Search Indonesian mutual funds (reksa dana) by name/manager words, category and Sharia flag, sorted by a metric."""
    funds, stale = _funds()
    cat = CATEGORY.get(category.lower().strip(), category) if category else ""
    words = query.lower().split()
    hits = [f for f in funds
            if (not cat or f.get("ConservativeCategory") == cat)
            and (sharia is None or bool(f.get("Sharia")) == sharia)
            and (f.get("AssetUnderManagement") or 0) >= min_aum_idr
            and all(w in (f["Name"] + " " + f["InvestmentManagerName"]).lower() for w in words)]
    key = SORT.get(sort_by, "YearlyReturn")
    hits = [f for f in hits if f.get(key) is not None]
    hits.sort(key=lambda f: f[key], reverse=True)
    return {"matches": len(hits), "sorted_by": sort_by,
            "funds": [_fund_view(f, stale) for f in hits[:max(1, min(limit, 10))]],
            "note": "Past returns do not guarantee future returns."}


def get_fund(name: str) -> dict:
    """Full data for one mutual fund by name. Returns suggestions when the name is not found."""
    funds, stale = _funds()
    by = {f["Name"].lower(): f for f in funds}
    key = name.lower().strip()
    if key in by:
        return _fund_view(by[key], stale)
    close = difflib.get_close_matches(key, list(by), n=5, cutoff=0.5)
    if close and difflib.SequenceMatcher(None, key, close[0]).ratio() >= 0.9:
        return {**_fund_view(by[close[0]], stale), "matched_from": name}
    return {"error": "not_found", "query": name, "suggestions": [by[c]["Name"] for c in close]}


def get_stock(code: str) -> dict:
    """Price, returns and valuation for one IDX stock by ticker (e.g. BBCA)."""
    rows, stale = fetch(STOCKS_URL)
    code = code.upper().strip()
    s = next((r for r in rows if r.get("Code") == code), None)
    if not s:
        sug = [r["Code"] for r in rows if code.lower() in (r.get("Name") or "").lower()][:5]
        return {"error": "not_found", "query": code, "suggestions": sug}
    return {
        "code": s["Code"], "name": s["Name"], "sector": s.get("NewSectorName"), "industry": s.get("NewSubIndustryName"),
        "price_idr": s.get("Last"), "price_date": (s.get("LastDate") or "")[:10],
        "return_1m_pct": pct(s.get("OneMonth")), "return_ytd_pct": pct(s.get("Ytd")),
        "return_1y_pct": pct(s.get("OneYear")), "return_3y_pct": pct(s.get("ThreeYear")),
        "pe_ratio": s.get("Per"), "pb_ratio": s.get("Pbr"), "market_cap_idr": s.get("Capitalization"),
        "high_52w_idr": s.get("AdjustedAnnualHighPrice"), "low_52w_idr": s.get("AdjustedAnnualLowPrice"),
        "free_float_pct": s.get("FreeFloatPct"), "source": "pasardana.id", "stale": stale,
    }


def get_bi_rate() -> dict:
    """Bank Indonesia policy rate (BI-Rate): latest decision and the last six."""
    html, stale = fetch(BI_URL, as_json=False)
    rows = []
    for tr in BeautifulSoup(html, "html.parser").select("table tr"):
        c = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(c) >= 3 and re.match(r"[\d.,]+\s*%", c[2]):
            rows.append({"decided_on": c[1], "rate_pct": float(c[2].rstrip(" %").replace(",", "."))})
    if not rows:
        return {"error": "parse_failed", "source": BI_URL}
    return {"latest": rows[0], "history": rows[:6], "source": BI_URL, "stale": stale}


def get_usd_idr() -> dict:
    """USD to IDR reference rate (European Central Bank, via frankfurter.app)."""
    d, stale = fetch(FX_URL, ttl_hours=6)
    return {"usd_idr": d["rates"]["IDR"], "date": d["date"], "source": "ECB via frankfurter.app", "stale": stale}


def project_savings(initial_idr: float, monthly_idr: float, annual_return_pct: float, years: float,
                    annual_inflation_pct: float = 0, tax_on_gain_pct: float = 0) -> dict:
    """Exact compound growth of a lump sum plus monthly deposits. Use this instead of doing math in your head."""
    n = round(years * 12)
    r = (1 + annual_return_pct / 100) ** (1 / 12) - 1  # effective monthly rate, so 12 months = the annual rate
    grow = (1 + r) ** n
    final = initial_idr * grow + (monthly_idr * (grow - 1) / r if r else monthly_idr * n)
    paid = initial_idr + monthly_idr * n
    gain = final - paid
    tax = max(gain, 0) * tax_on_gain_pct / 100
    after_tax = final - tax
    real = after_tax / (1 + annual_inflation_pct / 100) ** years
    return {"months": n, "total_deposited_idr": round(paid), "final_value_idr": round(final),
            "gain_idr": round(gain), "tax_idr": round(tax), "final_after_tax_idr": round(after_tax),
            "final_in_todays_money_idr": round(real),
            "assumptions": "Deposits at the end of each month, return compounded monthly, constant rates."}


def search_docs(query: str, k: int = 6) -> dict:
    """Search official OJK and LPS consumer-finance pages. Use for rules, definitions, risks and how products work."""
    # k=6, not 4: the long legal text on the LPS page repeats common words and pushed the passage that states
    # the Rp2 billion guarantee limit to 6th place (eval case docs-01, 2026-11-18).
    hits = docs.index().search(query, k=max(1, min(k, 8)))
    return {"results": [{"title": h["title"], "url": h["url"], "text": h["text"]} for h in hits]}


FUNCS = {f.__name__: f for f in (search_funds, get_fund, get_stock, get_bi_rate, get_usd_idr,
                                  project_savings, search_docs)}


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


S, N, B = {"type": "string"}, {"type": "number"}, {"type": "boolean"}
TOOLS = [
    _fn("search_funds", search_funds.__doc__, {
        "query": {**S, "description": "Words in the fund or manager name. Empty for all."},
        "category": {**S, "description": "money market, fixed income, equity, balanced, index, protected, global or etf"},
        "sharia": {**B, "description": "true for Sharia funds only, false for conventional only. Omit for both."},
        "sort_by": {**S, "enum": list(SORT)}, "limit": {"type": "integer"},
        "min_aum_idr": {**N, "description": "Minimum fund size in rupiah, e.g. 100000000000 for Rp100 billion"}}),
    _fn("get_fund", get_fund.__doc__, {"name": S}, ["name"]),
    _fn("get_stock", get_stock.__doc__, {"code": S}, ["code"]),
    _fn("get_bi_rate", get_bi_rate.__doc__, {}),
    _fn("get_usd_idr", get_usd_idr.__doc__, {}),
    _fn("project_savings", project_savings.__doc__, {
        "initial_idr": N, "monthly_idr": N, "annual_return_pct": N, "years": N,
        "annual_inflation_pct": N, "tax_on_gain_pct": {**N, "description": "e.g. 20 for deposit interest tax"}},
        ["initial_idr", "monthly_idr", "annual_return_pct", "years"]),
    _fn("search_docs", search_docs.__doc__, {"query": S, "k": {"type": "integer"}}, ["query"]),
]
