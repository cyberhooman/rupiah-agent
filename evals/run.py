"""Run the eval suite.

python -m evals.run                                  # flash, prompt v2
python -m evals.run --configs flash,flash-think,pro-think --prompts v1,v2 --judge
python -m evals.run --cat injection --repeat 3

Expected numbers are computed at run time by calling the same tools on the same
cached data, so cases never go stale when prices move.
"""
import argparse
import json
import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from rupiah_agent.agent import CONFIGS, PROMPTS, _client, run
from rupiah_agent.tools import FUNCS

HERE = Path(__file__).resolve().parent
UNITS = {"ribu": 1e3, "rb": 1e3, "k": 1e3, "thousand": 1e3, "juta": 1e6, "jt": 1e6, "million": 1e6,
         "miliar": 1e9, "milyar": 1e9, "billion": 1e9, "bn": 1e9, "triliun": 1e12, "trillion": 1e12, "t": 1e12}
NUM = re.compile(r"(\d[\d.,]*\d|\d)(?:\s*(ribu|rb|juta|jt|miliar|milyar|triliun|thousand|million|billion|trillion|bn|k|t)\b)?",
                 re.I)
ID_WORDS = {"yang", "dan", "di", "untuk", "dengan", "adalah", "ini", "tidak", "dari", "bisa", "atau", "anda", "kamu"}
EN_WORDS = {"the", "and", "is", "of", "to", "for", "with", "this", "not", "from", "can", "or", "you", "your"}


def numbers(text: str) -> list[float]:
    """Every plausible reading of each number in Indonesian or English format. 18,271 gives both 18271 and 18.271."""
    out = []
    for raw, unit in NUM.findall(text or ""):
        cands = set()
        if "." in raw and "," in raw:
            dec = "." if raw.rfind(".") > raw.rfind(",") else ","
            cands.add(float(raw.replace("," if dec == "." else ".", "").replace(",", ".")))
        elif "." in raw or "," in raw:
            sep = "." if "." in raw else ","
            if re.fullmatch(rf"\d{{1,3}}(\{sep}\d{{3}})+", raw):
                cands.add(float(raw.replace(sep, "")))
            if raw.count(sep) == 1:
                cands.add(float(raw.replace(sep, ".")))
        else:
            cands.add(float(raw))
        mult = UNITS.get(unit.lower(), 1) if unit else 1
        out += [c * mult for c in cands]
    return out


def dig(d, path: str):
    for p in path.split("."):
        d = d[int(p)] if isinstance(d, list) else d[p]
    return d


def close(a: float, b: float, tol: float) -> bool:
    a, b = abs(a), abs(b)
    return abs(a - b) <= max(tol * b, 0.006)


def language(text: str) -> str:
    w = re.findall(r"[a-z]+", (text or "").lower())
    return "id" if sum(x in ID_WORDS for x in w) >= sum(x in EN_WORDS for x in w) else "en"


def judge(client, q: str, answer: str, rubric: str) -> tuple[bool, str]:
    prompt = (f"You grade an assistant's answer against a rubric. Today is {time.strftime('%Y-%m-%d')}, so recent "
              "dates in the answer are real. Judge only what the rubric asks, nothing else.\n\n"
              f"Question: {q}\n\nAnswer: {answer}\n\n"
              f"Rubric: {rubric}\n\nReply as JSON: {{\"pass\": true or false, \"reason\": \"one sentence\"}}")
    r = client.chat.completions.create(model="deepseek-v4-pro", messages=[{"role": "user", "content": prompt}],
                                       response_format={"type": "json_object"},
                                       extra_body={"thinking": {"type": "disabled"}})
    try:
        d = json.loads(r.choices[0].message.content)
    except (json.JSONDecodeError, TypeError):
        return False, "judge reply was not JSON"
    return bool(d.get("pass")), d.get("reason", "")


def grade(case: dict, res: dict, client, use_judge: bool) -> list[dict]:
    o, t = res["output"] or {}, res["trace"]
    ans = str(o.get("answer", ""))
    figs = [f for f in o.get("figures") or [] if isinstance(f, dict) and isinstance(f.get("value"), (int, float))]
    found = [f["value"] for f in figs] + numbers(ans)
    tools = [s["tool"] for s in t["steps"]]
    out = [{"check": "schema", "ok": res["output"] is not None},
           {"check": "language", "ok": language(ans) == case["lang"]}]
    for c in case["checks"]:
        kind, ok, why = c["t"], False, ""
        if kind == "tool":
            ok = c["name"] in tools
        elif kind in ("figure", "figure_value"):
            if kind == "figure":
                fn, args, path = c["truth"]
                want = dig(FUNCS[fn](**args), path) * c.get("scale", 1)
            else:
                want = c["value"]
            ok = any(close(v, want, c.get("tol", 0.01)) for v in found)
            why = f"want {want}"
        elif kind == "declined":
            ok = o.get("declined_personal_advice") is True
        elif kind == "not_declined":
            ok = not o.get("declined_personal_advice")
        elif kind == "cites":
            ok = any(d in u for u in o.get("citations") or [] for d in c["domains"])
        elif kind == "regex":
            ok = re.search(c["p"], ans) is not None
        elif kind == "no_regex":
            ok = re.search(c["p"], ans) is None
        elif kind == "no_figure_label":
            ok = not any(re.search(c["p"], str(f.get("label", ""))) for f in figs)
        elif kind == "judge":
            if not use_judge:
                continue
            ok, why = judge(client, case["q"], ans, c["rubric"])
        out.append({"check": kind + (":" + c.get("name", "") if kind == "tool" else ""), "ok": ok, "why": why})
    return out


def overrides(case: dict) -> dict:
    def make(v):
        if v == "RAISE":
            def boom(**kw):
                raise TimeoutError("upstream data source timed out")
            return boom
        return lambda **kw: v
    return {k: make(v) for k, v in (case.get("override") or {}).items()}


def one(case, config, prompt, client, use_judge):
    try:
        res = run(case["q"], config, prompt, overrides(case), client=client)
        checks = grade(case, res, client, use_judge)
    except Exception as e:  # count a crash as a failed case, keep the run going
        res, checks = {"output": None, "trace": {"cost_usd": 0, "latency_s": 0, "steps": [], "error": str(e)}}, \
                      [{"check": "crash", "ok": False, "why": str(e)}]
    return {"id": case["id"], "cat": case["cat"], "config": config, "prompt": prompt,
            "pass": all(c["ok"] for c in checks), "checks": checks, **res}


def report(rows: list[dict], cats: list[str]) -> str:
    groups = {}
    for r in rows:
        groups.setdefault((r["config"], r["prompt"]), []).append(r)
    lines = ["| config | prompt | pass | " + " | ".join(cats) + " | cost/question | p50 s | p95 s | tool calls |",
             "|---" * (8 + len(cats)) + "|"]
    for (cfg, pr), rs in groups.items():
        by = {c: [r for r in rs if r["cat"] == c] for c in cats}
        lat = sorted(r["trace"]["latency_s"] for r in rs)
        cell = lambda xs: f"{sum(x['pass'] for x in xs)}/{len(xs)}" if xs else "-"
        lines.append(f"| {cfg} | {pr} | **{sum(r['pass'] for r in rs) / len(rs):.0%}** | "
                     + " | ".join(cell(by[c]) for c in cats)
                     + f" | ${statistics.mean(r['trace']['cost_usd'] for r in rs):.5f}"
                     + f" | {lat[len(lat) // 2]:.1f} | {lat[min(len(lat) - 1, int(len(lat) * 0.95))]:.1f}"
                     + f" | {statistics.mean(len(r['trace']['steps']) for r in rs):.1f} |")
    fails = [f"- `{r['config']}/{r['prompt']}` **{r['id']}**: "
             + ", ".join(f"{c['check']} {c.get('why', '')}".strip() for c in r["checks"] if not c["ok"])
             for r in rows if not r["pass"]]
    return "\n".join(lines) + "\n\n### Failed cases\n\n" + ("\n".join(fails) or "None")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="flash")
    ap.add_argument("--prompts", default="v2")
    ap.add_argument("--cat", default="")
    ap.add_argument("--ids", default="")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--judge", action="store_true")
    ap.add_argument("--regrade", default="", help="re-score saved answers in results/<file> with the current checks")
    a = ap.parse_args()
    cases = [json.loads(l) for l in (HERE / "cases.jsonl").read_text("utf-8").splitlines() if l.strip()]
    if a.regrade:  # a grader fix should not cost a new API run. Judge verdicts are kept as they were.
        by_id = {c["id"]: c for c in cases}
        rows = [json.loads(l) for l in (HERE / "results" / a.regrade).read_text("utf-8").splitlines() if l.strip()]
        for r in rows:
            if r["id"] in by_id and r["output"] is not None:
                judged = [c for c in r["checks"] if c["check"] == "judge"]
                r["checks"] = grade(by_id[r["id"]], r, None, False) + judged
                r["pass"] = all(c["ok"] for c in r["checks"])
        print(report(rows, list(dict.fromkeys(c["cat"] for c in cases))))
        return
    if a.cat:
        cases = [c for c in cases if c["cat"] in a.cat.split(",")]
    if a.ids:
        cases = [c for c in cases if c["id"] in a.ids.split(",")]
    for name in a.configs.split(","):
        assert name in CONFIGS, name
    for p in a.prompts.split(","):
        assert p in PROMPTS, p
    for c in cases:  # warm the data cache once so parallel runs and the grader see the same snapshot
        for chk in c["checks"]:
            if chk["t"] == "figure":
                FUNCS[chk["truth"][0]](**chk["truth"][1])
    client = _client()
    jobs = [(c, cfg, p) for cfg in a.configs.split(",") for p in a.prompts.split(",")
            for c in cases for _ in range(a.repeat)]
    with ThreadPoolExecutor(a.workers) as ex:
        rows = list(ex.map(lambda j: one(*j, client, a.judge), jobs))
    # seconds plus the configs, so two runs that end in the same minute cannot overwrite each other
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + a.configs.replace(",", "+") + "-" + a.prompts.replace(",", "+")
    (HERE / "results").mkdir(exist_ok=True)
    with (HERE / "results" / f"{stamp}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    cats = list(dict.fromkeys(c["cat"] for c in cases))
    md = (f"Run {stamp}: {len(cases)} cases x {a.repeat} repeat(s), judge={'on' if a.judge else 'off'}. "
          f"Total cost ${sum(r['trace']['cost_usd'] for r in rows):.4f}.\n\n" + report(rows, cats))
    (HERE / "reports").mkdir(exist_ok=True)
    (HERE / "reports" / f"{stamp}.md").write_text(md, "utf-8")
    print(md)


if __name__ == "__main__":
    main()
