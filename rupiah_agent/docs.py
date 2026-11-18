"""Search over official Indonesian consumer-finance pages (OJK Sikapiuangmu, LPS).

`python -m rupiah_agent.docs build` crawls the pages into data/corpus.jsonl.
The corpus is not committed: it is OJK/LPS content, so each user builds it locally.
Retrieval is BM25. No vector database: on a few thousand short passages it is fast,
needs no embedding API, and its hits are easy to explain.
"""
import json
import math
import re
import sys
import time
from collections import Counter
from functools import lru_cache
from pathlib import Path

from bs4 import BeautifulSoup

from .data import ROOT, fetch

CORPUS = ROOT / "data" / "corpus.jsonl"
OJK = "https://sikapiuangmu.ojk.go.id"
SEEDS = [f"{OJK}/FrontEnd/CMS/Category/{c}" for c in (62, 66)]
LPS_PAGES = ["https://lps.go.id/simpanan-yang-dijamin/", "https://lps.go.id/fungsi-utama-penjaminan-simpanan/"]
STOP = set("yang dan di ke dari untuk dengan ini itu atau pada adalah dalam juga tidak akan bisa ada oleh sebagai "
           "kita kamu anda sobat kami the a an of to in is are and or for on what how apa bagaimana berapa".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


def _article(html: str) -> tuple[str, str]:
    s = BeautifulSoup(html, "html.parser")
    h = s.find(["h1", "h2", "h3"])
    if not h:
        return "", ""
    paras = []
    for p in h.find_all_next(["p", "li"]):
        t = p.get_text(" ", strip=True)
        if t.startswith("Ingin bertanya"):  # footer starts here
            break
        if len(t) > 40 and t not in paras:
            paras.append(t)
    return h.get_text(" ", strip=True), "\n".join(paras)


def _chunks(title: str, body: str, url: str, words: int = 140):
    w = body.split()
    for i in range(0, len(w), words - 30):  # 30-word overlap so a fact is not cut in half
        yield {"title": title, "url": url, "text": " ".join(w[i:i + words])}
        if i + words >= len(w):
            break


def build(max_articles: int = 160):
    seen, queue, rows = set(), list(SEEDS), []
    articles = []
    while queue and len(articles) < max_articles:
        page = queue.pop(0)
        if page in seen:
            continue
        seen.add(page)
        html, _ = fetch(page, ttl_hours=24 * 7, as_json=False)
        for href in re.findall(r'href="(/FrontEnd/CMS/(?:Article|Category)/\d+)"', html):
            (articles if "/Article/" in href else queue).append(OJK + href)
        articles = list(dict.fromkeys(articles))
        time.sleep(1)
    for url in articles[:max_articles] + LPS_PAGES:
        try:
            html, _ = fetch(url, ttl_hours=24 * 7, as_json=False)
        except RuntimeError as e:
            print("skip", url, e, file=sys.stderr)
            continue
        title, body = _article(html)
        if len(body) > 200:
            rows += list(_chunks(title, body, url))
        time.sleep(1)
    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    with CORPUS.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{len(rows)} passages from {len({r['url'] for r in rows})} pages -> {CORPUS}")


class BM25:
    def __init__(self, docs: list[dict], k1: float = 1.5, b: float = 0.75):
        self.docs, self.k1, self.b = docs, k1, b
        self.tf = [Counter(tokens(d["title"] + " " + d["text"])) for d in docs]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = sum(self.len) / max(len(docs), 1)
        df = Counter(w for t in self.tf for w in t)
        n = len(docs)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, k: int = 4) -> list[dict]:
        q = tokens(query)
        scored = []
        for i, tf in enumerate(self.tf):
            s = sum(self.idf.get(w, 0) * tf[w] * (self.k1 + 1) /
                    (tf[w] + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)) for w in q if w in tf)
            if s > 0:
                scored.append((s, i))
        scored.sort(reverse=True)
        out, urls = [], set()
        for s, i in scored:
            if self.docs[i]["url"] in urls:  # one passage per page keeps the context varied
                continue
            urls.add(self.docs[i]["url"])
            out.append({**self.docs[i], "score": round(s, 2)})
            if len(out) == k:
                break
        return out


@lru_cache(maxsize=1)
def index() -> BM25:
    if not CORPUS.exists():
        raise RuntimeError("No corpus yet. Run: python -m rupiah_agent.docs build")
    return BM25([json.loads(l) for l in CORPUS.read_text("utf-8").splitlines() if l.strip()])


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        build()
    else:
        for hit in index().search(" ".join(sys.argv[1:])):
            print(hit["score"], hit["title"], hit["url"])
