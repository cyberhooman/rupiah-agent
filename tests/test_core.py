"""Offline tests: no network, no API key. Run: pytest -q"""
import json
from types import SimpleNamespace as NS

import pytest

from evals.run import close, grade, language, numbers
from rupiah_agent import agent, tools
from rupiah_agent.docs import BM25

FUNDS = [
    {"Name": "Alpha Pasar Uang", "InvestmentManagerName": "Alpha AM", "ConservativeCategory": "Pasar Uang",
     "Sharia": False, "NetAssetValue": 1500.5, "YearlyReturn": 0.051, "AssetUnderManagement": 2e12},
    {"Name": "Beta Kas Syariah", "InvestmentManagerName": "Beta AM", "ConservativeCategory": "Pasar Uang",
     "Sharia": True, "NetAssetValue": 1200.0, "YearlyReturn": 0.046, "AssetUnderManagement": 5e11},
    {"Name": "Gamma Saham", "InvestmentManagerName": "Gamma AM", "ConservativeCategory": "Saham",
     "Sharia": False, "NetAssetValue": 3000.0, "YearlyReturn": -0.12, "AssetUnderManagement": 1e12},
    {"Name": "IDX Index Row", "InvestmentManagerName": None},  # index rows have no manager and are dropped
]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(tools, "fetch", lambda url, **kw: (FUNDS, False))


def test_project_savings_matches_closed_form():
    r = tools.project_savings(10_000_000, 0, 6, 1)
    assert r["final_value_idr"] == 10_600_000  # 12 monthly steps compound to exactly the annual rate
    r = tools.project_savings(0, 1_000_000, 0, 2)
    assert r["final_value_idr"] == 24_000_000 and r["gain_idr"] == 0
    r = tools.project_savings(100_000_000, 0, 4.5, 1, tax_on_gain_pct=20)
    assert r["final_after_tax_idr"] == 103_600_000
    r = tools.project_savings(100, 0, 0, 1, annual_inflation_pct=25)
    assert r["final_in_todays_money_idr"] == 80


def test_search_funds_filters_and_sorts():
    r = tools.search_funds(category="money market", sort_by="return_1y")
    assert [f["name"] for f in r["funds"]] == ["Alpha Pasar Uang", "Beta Kas Syariah"]
    assert tools.search_funds(category="pasar uang", sharia=True)["matches"] == 1
    assert tools.search_funds(min_aum_idr=1e12)["matches"] == 2
    assert tools.search_funds(query="gamma")["funds"][0]["return_1y_pct"] == -12.0


def test_get_fund_exact_fuzzy_and_not_found():
    assert tools.get_fund("alpha pasar uang")["nav"] == 1500.5
    assert tools.get_fund("Alpha Pasar Uangg")["matched_from"] == "Alpha Pasar Uangg"
    miss = tools.get_fund("Reksa Dana Pasti Cuan")
    assert miss["error"] == "not_found"


def test_bm25_ranks_the_matching_passage_first():
    idx = BM25([{"title": "Deposito", "url": "u1", "text": "deposito dijamin LPS sampai 2 miliar"},
                {"title": "Saham", "url": "u2", "text": "saham adalah bukti kepemilikan perusahaan"}])
    assert idx.search("berapa jaminan LPS deposito")[0]["url"] == "u1"
    assert idx.search("zzz") == []


@pytest.mark.parametrize("text,value", [
    ("NAB Rp1.500,50 per unit", 1500.5), ("total Rp180.381.919", 180381919), ("sekitar 180,38 juta", 180.38e6),
    ("BI-Rate 5,75%", 5.75), ("USD 1 = Rp18,271", 18271), ("AUM Rp1,35 triliun", 1.35e12), ("P/E 11.31x", 11.31),
])
def test_numbers_reads_indonesian_and_english_formats(text, value):
    assert any(close(v, value, 0.001) for v in numbers(text))


def test_language_detector():
    assert language("Ini adalah jawaban yang singkat untuk kamu") == "id"
    assert language("This is the short answer for you") == "en"


def fake_client(replies):
    """A stand-in for the OpenAI client that returns scripted messages."""
    usage = NS(prompt_tokens=100, completion_tokens=20, model_extra={"prompt_cache_hit_tokens": 0,
                                                                     "prompt_cache_miss_tokens": 100})
    it = iter(replies)
    create = lambda **kw: NS(choices=[NS(message=next(it))], usage=usage)
    return NS(chat=NS(completions=NS(create=create)))


def msg(content="", tool=None, args=None):
    calls = None
    if tool:
        fn = NS(name=tool, arguments=json.dumps(args or {}))
        calls = [NS(id="c1", function=fn, model_dump=lambda **kw: {"id": "c1", "type": "function",
                                                                     "function": {"name": tool, "arguments": "{}"}})]
    return NS(content=content, tool_calls=calls, model_extra={})


def test_agent_loop_calls_tool_then_answers_and_grades():
    final = json.dumps({"answer": "NAB Alpha Pasar Uang Rp1.500,50 per 17 Nov.", "language": "id",
                        "citations": ["pasardana.id"], "figures": [{"label": "NAB", "value": 1500.5}],
                        "declined_personal_advice": False})
    client = fake_client([msg(tool="get_fund", args={"name": "Alpha Pasar Uang"}), msg(final)])
    res = agent.run("Berapa NAB Alpha Pasar Uang?", client=client)
    assert res["output"]["figures"][0]["value"] == 1500.5
    assert [s["tool"] for s in res["trace"]["steps"]] == ["get_fund"] and res["trace"]["steps"][0]["ok"]
    assert res["trace"]["cost_usd"] > 0
    case = {"q": "x", "lang": "id", "checks": [{"t": "tool", "name": "get_fund"}, {"t": "not_declined"},
                                                {"t": "figure", "truth": ["get_fund", {"name": "Alpha Pasar Uang"}, "nav"]}]}
    assert all(c["ok"] for c in grade(case, res, None, False))


def test_agent_survives_a_crashing_tool_and_repairs_bad_json():
    good = json.dumps({"answer": "Data is unavailable right now.", "language": "en", "citations": [],
                       "figures": [], "declined_personal_advice": False})
    client = fake_client([msg(tool="get_stock", args={"code": "BBCA"}), msg("not json"), msg(good)])

    def boom(**kw):
        raise TimeoutError("down")

    res = agent.run("BBCA price?", tool_overrides={"get_stock": boom}, client=client)
    assert res["trace"]["steps"][0]["ok"] is False and "TimeoutError" in res["trace"]["steps"][0]["result"]
    assert res["trace"]["repaired"] and res["output"]["answer"].startswith("Data is unavailable")
