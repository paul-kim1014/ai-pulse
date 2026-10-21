"""기간별·축별 점수와 순위 — 기획서 3.3 공식 / 5.1 기간 정의.

- 저장소·모델: 기간 증가량(두 스냅샷 차이). 기준일 스냅샷이 아직 없으면(콜드 스타트)
  '그 기간에 새로 공개된 항목' 코호트로 평가한다. 이 경우 누적값 = 기간 내 증가량이므로 정확하다.
- 논문: 항상 공개 시기 코호트.
- 인기: 소스 전체 그룹에서 백분위(분야 편중도 정보로 보여줌). 영향력: 분야×코호트 그룹 백분위.
"""
import json
import math
from collections import defaultdict
from datetime import date, timedelta

from src.scoring import anomaly
from src.scoring.normalize import percentiles, weighted
from src.util import cfg

PERIODS = ["weekly", "monthly", "yearly", "total"]
AXES = ["popularity", "influence"]
SOURCES = ["github", "hf_model", "hf_dataset", "paper"]
WKEY = {"github": "github", "hf_model": "huggingface", "hf_dataset": "huggingface", "paper": "papers"}


def period_windows(today):
    first = today.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    jan1 = date(today.year, 1, 1)
    return {
        "weekly": {"start": today - timedelta(days=7), "end": today - timedelta(days=1),
                   "base": today - timedelta(days=7), "cur": today,
                   "label": f"{(today - timedelta(days=7)).strftime('%m/%d')}–{(today - timedelta(days=1)).strftime('%m/%d')}",
                   "short": "7일"},
        "monthly": {"start": prev_first, "end": first - timedelta(days=1), "base": prev_first, "cur": first,
                    "label": f"{prev_first.year}년 {prev_first.month}월", "short": f"{prev_first.month}월"},
        "yearly": {"start": jan1, "end": today - timedelta(days=1), "base": jan1, "cur": today,
                   "label": f"{today.year}.1.1–{(today - timedelta(days=1)).strftime('%m/%d')}", "short": "올해"},
        "total": {"start": date(1990, 1, 1), "end": today, "base": None, "cur": today,
                  "label": "전체 기간", "short": "누적"},
    }


class Snaps:
    """필요한 날짜 범위의 스냅샷만 메모리에 올린다."""

    def __init__(self, con, today, windows):
        dates = {today - timedelta(days=i) for i in range(0, 36)}
        for w in windows.values():
            for k in ("base", "cur"):
                if w[k]:
                    dates |= {w[k] - timedelta(days=i) for i in range(0, 4)}
        self.d = defaultdict(lambda: defaultdict(dict))
        q = ",".join("?" * len(dates))
        for r in con.execute(f"SELECT item_id, date, metric, value FROM snapshots WHERE date IN ({q})",
                             [d.isoformat() for d in dates]):
            self.d[r[0]][r[2]][r[1]] = r[3]
        self.dates_by_source = defaultdict(set)

    def at(self, iid, metric, day, tol=3):
        """day 당일 또는 그 이전 tol일 이내 가장 가까운 값."""
        s = self.d.get(iid, {}).get(metric)
        if not s:
            return None
        for i in range(tol + 1):
            v = s.get((day - timedelta(days=i)).isoformat())
            if v is not None:
                return v
        return None

    def latest(self, iid, metric, day):
        return self.at(iid, metric, day, tol=35)

    def series(self, iid, metric):
        return sorted(self.d.get(iid, {}).get(metric, {}).items())

    def window_avg(self, iid, metric, start, end):
        s = self.d.get(iid, {}).get(metric, {})
        vals = [v for d, v in s.items() if start.isoformat() <= d <= end.isoformat()]
        return sum(vals) / len(vals) if vals else None


def _months_since(created, today):
    if not created:
        return None
    return max((today - created).days / 30.44, 1.0)


def delta_mode(snaps, items, source, metric, w):
    """기준일 스냅샷이 해당 소스 항목의 절반 이상에 있으면 증가량 모드."""
    if w["base"] is None:
        return "total"
    ids = [i for i, it in items.items() if it["source"] == source]
    if not ids:
        return "cohort"
    have = sum(1 for i in ids if snaps.at(i, metric, w["base"]) is not None)
    return "delta" if have >= 0.5 * len(ids) else "cohort"


def _d(snaps, iid, metric, w):
    a, b = snaps.at(iid, metric, w["cur"]), snaps.at(iid, metric, w["base"])
    return None if a is None or b is None else max(a - b, 0)


def raw_metrics(source, iid, it, snaps, w, mode, period, today):
    """한 항목의 해당 기간 원지표 {axis: {metric: value}} 와 표시용 요약."""
    L = lambda m: snaps.latest(iid, m, today)  # noqa: E731
    pop, inf = {}, {}
    if source == "github":
        if mode == "delta":
            pop = {"stars_delta": _d(snaps, iid, "stars", w), "forks_delta": _d(snaps, iid, "forks", w),
                   "issues_prs_delta": _d(snaps, iid, "issues_prs", w)}
        else:  # cohort / total: 누적값
            pop = {"stars_delta": L("stars"), "forks_delta": L("forks"), "issues_prs_delta": L("issues_prs")}
        commits, age = L("commits_1y"), L("days_since_push")
        health = None
        if commits is not None and age is not None:
            rec = 1.0 if age <= 30 else 0.6 if age <= 90 else 0.3 if age <= 365 else 0.05
            health = math.expm1(math.log1p(commits) * rec)  # 최근성으로 감쇠한 커밋 수
        inf = {"dependents": None, "package_downloads": None, "contributors_1y": L("contributors"),
               "health": health, "forks_total": L("forks")}
    elif source in ("hf_model", "hf_dataset"):
        if mode == "delta":
            likes = _d(snaps, iid, "likes", w)
            trend = snaps.window_avg(iid, "trending_score", w["start"], w["end"] + timedelta(days=1))
            dl = snaps.at(iid, "downloads_30d", w["cur"])
        elif mode == "cohort":
            likes = L("likes")
            trend = L("trending_score") if period == "weekly" else None
            dl = L("downloads_30d")
        else:  # total
            likes, trend = L("likes"), None
            dl = L("downloads_all") or L("downloads_30d")
        pop = {"likes_delta": likes, "trending_score": trend, "downloads_30d": dl}
        inf = {"derivatives": L("derivatives"), "family_downloads": L("family_downloads"),
               "spaces": L("spaces") if source == "hf_model" else None, "likes_total": L("likes")}
    else:  # paper
        months = _months_since(it["created"], today)
        cit = L("citations")
        old = snaps.at(iid, "citations", today - timedelta(days=30), tol=3)
        vel = (cit - old) if (cit is not None and old is not None) else (cit / months if cit is not None and months else None)
        pop = {"hf_upvotes": L("hf_upvotes"), "code_stars_delta": L("code_stars"), "citation_velocity": vel}
        inf = {"influential_citations": L("influential_citations"),
               "age_norm_citations": (cit / months) if (cit is not None and months) else None,
               "code_model_adoption": None,  # 소스 점수 산출 후 채움
               "venue": L("venue")}
    return {"popularity": pop, "influence": inf}


def eligible(it, w, mode, period):
    if period == "total" or mode == "delta":
        return True
    c = it["created"]
    return c is not None and w["start"] <= c <= w["cur"]


def compute(con, today):
    """모든 소스·기간·축의 점수. 반환: (results, windows, modes, items)
    results[(source, period, axis)] = {item_id: {"score", "raw", "flag"}}"""
    W = cfg("weights")
    windows = period_windows(today)
    items = {}
    for r in con.execute("SELECT item_id, source, field, tags, created_at, arxiv_id, extra FROM items "
                         "WHERE is_active=1 AND field IS NOT NULL"):
        c = None
        if r["created_at"]:
            try:
                c = date.fromisoformat(r["created_at"][:10])
            except ValueError:
                c = None
        items[r["item_id"]] = {"source": r["source"], "field": r["field"], "created": c,
                               "tags": json.loads(r["tags"] or "[]"), "arxiv": r["arxiv_id"],
                               "extra": json.loads(r["extra"] or "{}")}
    snaps = Snaps(con, today, windows)
    modes = {}
    for p, w in windows.items():
        for s in SOURCES:
            if s == "paper":
                modes[(s, p)] = "total" if p == "total" else "cohort"
            else:
                metric = "stars" if s == "github" else "likes"
                modes[(s, p)] = delta_mode(snaps, items, s, metric, w)

    flags = anomaly.detect(snaps, items, today)
    results = {}
    raws = {}
    for s in SOURCES:
        ids = [i for i, it in items.items() if it["source"] == s]
        for p, w in windows.items():
            mode = modes[(s, p)]
            elig = [i for i in ids if eligible(items[i], w, mode, p)]
            raws[(s, p)] = {i: raw_metrics(s, i, items[i], snaps, w, mode, p, today) for i in elig}
            if mode == "delta":  # 영향력은 기간 내 활동이 있었던 항목 대상
                key = "stars_delta" if s == "github" else "likes_delta"
                raws[(s, p)] = {i: m for i, m in raws[(s, p)].items()
                                if (m["popularity"].get(key) or 0) > 0 or p == "total"}
    anomaly.download_inflation(raws, items, W["anomaly"]["download_ratio_bottom_pct"])

    def score_axis(s, p, axis):
        wts = W[WKEY[s]][axis]
        rm = raws[(s, p)]
        if axis == "popularity":
            groups = {"_all": list(rm)}
        else:
            groups = defaultdict(list)
            for i in rm:
                groups[items[i]["field"]].append(i)
        out = {}
        for g, members in groups.items():
            pcts = {m: percentiles({i: rm[i][axis].get(m) for i in members}) for m in wts}
            for i in members:
                sc, cover = weighted(pcts, wts, i)
                if sc is None:
                    continue
                flag = i in flags
                if axis == "popularity" and flag:
                    sc *= W["anomaly"]["popularity_penalty"]
                out[i] = {"score": round(sc, 2), "raw": rm[i][axis], "flag": flag, "cover": round(cover, 2)}
        return out

    for s in ("github", "hf_model", "hf_dataset"):
        for p in windows:
            for a in AXES:
                results[(s, p, a)] = score_axis(s, p, a)

    # 논문 '코드·모델 채택' = 연결된 저장소·모델의 누적 영향력 점수 최댓값 (플랫폼 교차 검증)
    adopt = defaultdict(float)
    for s in ("github", "hf_model"):
        for i, v in results[(s, "total", "influence")].items():
            it = items[i]
            aid = it["arxiv"] or it["extra"].get("paper")
            if aid:
                adopt[f"paper:{aid}"] = max(adopt[f"paper:{aid}"], v["score"])
    for p in windows:
        for i, m in raws[("paper", p)].items():
            m["influence"]["code_model_adoption"] = adopt.get(i)
        for a in AXES:
            results[("paper", p, a)] = score_axis("paper", p, a)
    return results, windows, modes, items, snaps, flags
