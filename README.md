# Rupiah Agent

An AI agent that answers questions about money in Indonesia, in Bahasa Indonesia or English, with live data.

Ask it things like:

- "Berapa NAB Sucorinvest Money Market Fund sekarang?"
- "Reksa dana pasar uang di atas 1 triliun, mana yang return setahunnya paling tinggi?"
- "Deposito 100 juta bunga 4,5%, pajak 20%. Setelah setahun bersihnya berapa?"
- "Berapa batas simpanan yang dijamin LPS?"

It explains and compares. It does not tell you what to buy.

## What it can use

| Tool | Data |
|---|---|
| `search_funds`, `get_fund` | 1,500+ Indonesian mutual funds from pasardana.id: NAV, returns, fund size, fees, drawdown, Sharia flag |
| `get_stock` | IDX stocks: price, returns, P/E, P/B, market cap |
| `get_bi_rate` | Bank Indonesia policy rate, from bi.go.id |
| `get_usd_idr` | USD to IDR reference rate (ECB) |
| `project_savings` | Exact compound growth with monthly deposits, tax and inflation |
| `search_docs` | Search over official OJK (Sikapiuangmu) and LPS pages, with links |

The same tools also run as an MCP server, so Claude Desktop or Claude Code can use them:

```
claude mcp add rupiah -- python -m rupiah_agent.mcp_server
```

## How it works

- The model calls tools in a loop, up to 6 steps. A tool that fails goes back to the model as an error, so one broken source does not crash the answer.
- Data is cached on disk. If a source is down, the agent uses the last copy and says it may be out of date.
- Every answer is a JSON object: the reply, the language, the sources, each number with its date, and a flag for when it declined to give personal advice. If the model returns bad JSON, it gets one chance to fix it.
- Document search is BM25 over 147 OJK and LPS pages (861 passages). No vector database. For a few thousand short passages it is fast, needs no extra API, and it is easy to see why a page came up.
- Each run logs every tool call with its time, plus tokens, cost and total time.

## Evals

`evals/cases.jsonl` has 60 cases in 11 groups. Most expected numbers are not written in the file. The grader calls the same tool on the same data when the test runs, so the tests keep working when prices change.

| Group | What it checks |
|---|---|
| fund_lookup, stock_lookup, rates | The number in the answer matches the live data |
| calc | Projections match the calculator to within 0.5% |
| screen | Filtering and sorting funds gives the right top result |
| docs | Answers about rules cite an OJK or LPS page |
| advice_guardrail | "What should I buy" gets an explanation, not a pick |
| not_found | Fake funds and tickers get "not found", not a made up number |
| injection | Hidden instructions in a document or data result are ignored |
| reliability | A source that is down or out of date is handled and mentioned |
| language | It replies in the language you wrote in |

Some checks also catch refusing too much: plain factual questions must not be declined. A few open questions are graded by a second model against a written rubric (`--judge`).

### Results (18 Nov 2026, full report in [evals/REPORT.md](evals/REPORT.md))

| setup | pass | cost per question | median time |
|---|---|---|---|
| deepseek-flash, naive prompt | 68% | $0.0010 | 3.9 s |
| deepseek-flash, prompt with rules | 97% | $0.0012 | 3.7 s |
| deepseek-flash, rules + fixes from the evals | **98%** | $0.0012 | 3.7 s |
| deepseek-flash, thinking | 93% | $0.0017 | 5.9 s |
| deepseek-v4-pro, thinking | 87% | $0.0054 | 8.6 s |

What the evals taught me:

- Rules in the prompt took it from 68% to 97%. Most of the gain was answering in the right language.
- The bigger model did worse and cost 4.6 times more. It kept answering English questions in Indonesian.
- They found a search bug: the passage with the Rp2 billion LPS limit ranked 6th, behind legal text from the LPS
  page that repeats the same words. It now returns 6 passages.
- They found my own grader bugs too, like a regex that failed the agent for quoting a scam phrase while warning
  about it. Saved answers can be re-scored for free with `--regrade`.

## Run it

```
pip install -r requirements.txt
cp .env.example .env            # add your DeepSeek API key
python -m rupiah_agent.docs build   # fetch the OJK and LPS pages, about 5 minutes
python -m rupiah_agent.agent "Berapa BI-Rate sekarang?"
python -m evals.run --configs flash,flash-think,pro-think --prompts v1,v2 --judge
pytest -q                       # offline tests, no key needed
```

## Notes

- Not financial advice. Data comes from public sources and can be late or wrong.
- The OJK and LPS pages are not in this repo. `docs build` fetches them to your machine.
