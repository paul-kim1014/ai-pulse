"""순위 산정 — 소스별 순위 + 연구 패밀리 단위 통합 순위, 표시용 핵심 지표 문구."""
from collections import defaultdict
from datetime import timedelta

from src.scoring.score import AXES, PERIODS, SOURCES
from src.util import cfg

TIEBREAK = {"github": "stars_delta", "hf_model": "likes_delta", "hf_dataset": "likes_delta", "paper": "hf_upvotes"}
INF_TIEBREAK = {"github": "contributors_1y", "hf_model": "derivatives", "hf_dataset": "derivatives",
                "paper": "influential_citations"}


def fmt(n):
    if n is None:
        return "–"
    n = float(n)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e4:
        return f"{n / 1e3:.0f}k"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:,.0f}"


def metric_text(source, axis, mode, short, raw, snaps=None, iid=None, today=None):
    sign = "+" if mode == "delta" else ""
    tail = f" ({short})" if mode == "delta" else (" · 신규" if mode == "cohort" else "")
    if source == "github":
        if axis == "popularity":
            return f"★ {sign}{fmt(raw.get('stars_delta'))}{tail}"
        return f"기여자 {fmt(raw.get('contributors_1y'))} · 포크 {fmt(raw.get('forks_total'))}"
    if source in ("hf_model", "hf_dataset"):
        if axis == "popularity":
            if raw.get("likes_delta") is None and raw.get("downloads_30d") is not None:
                return f"↓ {fmt(raw.get('downloads_30d'))}/30일"
            return f"♥ {sign}{fmt(raw.get('likes_delta'))}{tail}"
        d = raw.get("derivatives")
        der = "1000+" if d and d >= 1000 else fmt(d)
        sp = raw.get("spaces")
        return f"파생 {der}" + (f" · Spaces {'100+' if sp and sp >= 100 else fmt(sp)}" if sp is not None else
                                f" · ↓ {fmt(raw.get('family_downloads'))}")
    if axis == "popularity":
        up = raw.get("hf_upvotes")
        cs = raw.get("code_stars_delta")
        if up is not None:
            return f"▲ {fmt(up)} 업보트" + (f" · ★ {fmt(cs)}" if cs else "")
        return f"인용 {fmt(raw.get('citation_velocity'))}/월"
    if snaps is not None:
        cit = snaps.latest(iid, "citations", today)
        return f"인용 {fmt(cit)} (핵심 {fmt(raw.get('influential_citations'))})"
    return f"핵심 인용 {fmt(raw.get('influential_citations'))}"


def _sort(res, source, axis):
    tb = TIEBREAK[source] if axis == "popularity" else INF_TIEBREAK[source]
    return sorted(res.items(), key=lambda kv: (-kv[1]["score"], -(kv[1]["raw"].get(tb) or 0)))


def build_families(items):
    """arXiv ID로 논문·코드·모델을 '연구 패밀리'로 묶는다.
    모델은 논문보다 400일 넘게 늦게 나온 경우(단순 인용 태그) 연결하지 않는다."""
    papers = {it["arxiv"]: i for i, it in items.items() if it["source"] == "paper" and it["arxiv"]}
    fam = {}
    for i, it in items.items():
        aid = it["arxiv"] or it["extra"].get("paper")
        if it["source"] != "paper" and aid in papers:
            p = items[papers[aid]]
            if it["source"] != "github" and it["created"] and p["created"] and \
                    it["created"] - p["created"] > timedelta(days=400):
                fam[i] = i
                continue
            fam[i] = f"paper:{aid}"
        else:
            fam[i] = i
    members = defaultdict(list)
    for i, f in fam.items():
        members[f].append(i)
    return fam, members


def rank_all(results, windows, modes, items, snaps, today):
    """반환: lists[(view, period, axis)] = [{id, score, text, fam, flag}, ...] (상위 list_size)"""
    n = cfg("sources")["export"]["list_size"]
    fw = cfg("weights")["family"]
    fam, members = build_families(items)
    lists = {}
    for p in PERIODS:
        short = windows[p]["short"]
        for a in AXES:
            for s in SOURCES:
                res = results[(s, p, a)]
                mode = modes[(s, p)]
                lst = []
                for iid, v in _sort(res, s, a)[:n]:
                    lst.append({"id": iid, "score": v["score"], "flag": v["flag"],
                                "text": metric_text(s, a, mode, short, v["raw"], snaps, iid, today)})
                lists[(s, p, a)] = lst
            # 통합: 패밀리 점수 = 소스별 점수 최댓값 70% + 평균 30%
            fam_scores = defaultdict(list)
            for s in SOURCES:
                for iid, v in results[(s, p, a)].items():
                    fam_scores[fam[iid]].append((v["score"], iid, s, v))
            agg = []
            for f, lst in fam_scores.items():
                lst.sort(key=lambda x: -x[0])
                best = lst[0]
                sc = fw["max_weight"] * best[0] + fw["mean_weight"] * (sum(x[0] for x in lst) / len(lst))
                tb = best[3]["raw"].get(TIEBREAK[best[2]] if a == "popularity" else INF_TIEBREAK[best[2]]) or 0
                agg.append((sc, tb, best, [x[1] for x in lst]))
            agg.sort(key=lambda x: (-x[0], -x[1]))
            out = []
            for sc, _, best, mem in agg[:n]:
                s, iid, v = best[2], best[1], best[3]
                out.append({"id": iid, "score": round(sc, 2), "flag": v["flag"],
                            "text": metric_text(s, a, modes[(s, p)], short, v["raw"], snaps, iid, today),
                            "fam": [m for m in members[fam[iid]] if m != iid][:6]})
            lists[("integrated", p, a)] = out
    return lists, fam, members
