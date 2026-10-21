"""대시보드용 JSON 생성 (docs/data/dashboard.json)."""
import json
from datetime import datetime

from src.scoring.score import AXES, PERIODS
from src.util import KST, ROOT, cfg, dump_json

VIEWS = ["integrated", "github", "hf_model", "hf_dataset", "paper"]
MAIN_METRIC = {"github": ["stars", "forks"], "hf_model": ["likes", "downloads_30d"],
               "hf_dataset": ["likes", "downloads_30d"], "paper": ["hf_upvotes", "citations"]}
SHOW_METRICS = {
    "github": ["stars", "forks", "watchers", "issues_prs", "contributors", "commits_1y", "days_since_push"],
    "hf_model": ["likes", "downloads_30d", "downloads_all", "trending_score", "derivatives", "family_downloads", "spaces"],
    "hf_dataset": ["likes", "downloads_30d", "downloads_all", "trending_score", "derivatives", "family_downloads"],
    "paper": ["hf_upvotes", "comments", "code_stars", "citations", "influential_citations", "venue"],
}


def displayed_ids(lists, items):
    """필터(전체·분야 6개·관심 태그)별 상위 10위에 노출될 수 있는 항목 — 한국어 설명 생성 대상."""
    top_n = cfg("sources")["export"]["top_n"]
    fields = cfg("fields")["order"]
    tags = list(cfg("sources")["interest_tags"])
    out, seen = [], set()
    for rank_cut in range(top_n):  # 상위 순위부터 채워 상한에 걸려도 중요한 것 먼저
        for lst in lists.values():
            for flt in [None] + fields + tags:
                sel = [e for e in lst if flt is None or items[e["id"]]["field"] == flt
                       or flt in items[e["id"]]["tags"]]
                if rank_cut < len(sel):
                    iid = sel[rank_cut]["id"]
                    if iid not in seen:
                        seen.add(iid)
                        out.append(iid)
                    for m in sel[rank_cut].get("fam", []):
                        if m not in seen:
                            seen.add(m)
                            out.append(m)
    return out


def item_meta(con, ids, snaps, today):
    out = {}
    if not ids:
        return out
    q = ",".join("?" * len(ids))
    for r in con.execute(f"SELECT * FROM items WHERE item_id IN ({q})", list(ids)):
        ex = json.loads(r["extra"] or "{}")
        src = r["source"]
        d = r["desc_ko"] or (ex.get("ai_summary") or r["description"] or "")[:110]
        links = {}
        if ex.get("hf_url"):
            links["HF Papers"] = ex["hf_url"]
        if ex.get("repo"):
            links["코드"] = f"https://github.com/{ex['repo']}"
        if src != "paper" and r["arxiv_id"]:
            links["논문"] = f"https://arxiv.org/abs/{r['arxiv_id']}"
        m = {k: snaps.latest(r["item_id"], k, today) for k in SHOW_METRICS[src]}
        out[r["item_id"]] = {
            "t": r["title"] or r["name"], "s": src, "f": r["field"], "g": json.loads(r["tags"] or "[]"),
            "d": d, "ko": bool(r["desc_ko"]), "u": r["url"], "c": r["created_at"], "l": links,
            "m": {k: v for k, v in m.items() if v is not None},
            "v": ex.get("venue"), "cat": (ex.get("cats") or [None])[0], "pt": ex.get("pipeline_tag"),
            "kw": json.loads(r["keywords"] or "[]")[:8],
        }
    return out


def history(con, ids):
    """상세 패널 추이 그래프용 일별 시계열 (주요 지표 2개)."""
    out = {}
    for iid in ids:
        src = iid.split(":", 1)[0]
        mets = MAIN_METRIC.get(src, [])
        rows = con.execute(f"SELECT metric, date, value FROM snapshots WHERE item_id=? AND metric IN "
                           f"({','.join('?' * len(mets))}) ORDER BY date", [iid, *mets]).fetchall()
        h = {}
        for r in rows:
            h.setdefault(r["metric"], []).append([r["date"], r["value"]])
        if any(len(v) >= 2 for v in h.values()):
            out[iid] = h
    return out


def export(con, today, lists, prev_lists, windows, modes, items, snaps, trends, status):
    fields = cfg("fields")
    ids = set()
    for lst in lists.values():
        for e in lst:
            ids.add(e["id"])
            ids.update(e.get("fam", []))
    meta = item_meta(con, ids, snaps, today)
    # 화면 필터별 순위 변동 계산용: 전일 목록 항목의 분야·태그
    prev_ids = {i for lst in prev_lists.values() for i in lst}
    prev_meta = {}
    for r in con.execute("SELECT item_id, field, tags FROM items"):
        if r["item_id"] in prev_ids:
            prev_meta[r["item_id"]] = (r["field"], json.loads(r["tags"] or "[]"))
    top10 = set()
    for lst in lists.values():
        top10.update(e["id"] for e in lst[:10])
    # 2×2 매트릭스: 통합 인기·영향력 상위 항목의 두 점수
    matrix = {}
    for p in PERIODS:
        P = {e["id"]: e["score"] for e in lists[("integrated", p, "popularity")]}
        I = {e["id"]: e["score"] for e in lists[("integrated", p, "influence")]}
        pick = [e["id"] for e in lists[("integrated", p, "popularity")][:40]] + \
               [e["id"] for e in lists[("integrated", p, "influence")][:40]]
        matrix[p] = [[i, P.get(i, 0), I.get(i, 0)] for i in dict.fromkeys(pick) if i in P and i in I]
    data = {
        "generated_at": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "run_date": today.isoformat(),
        "periods": {p: {"label": w["label"], "modes": {s: modes[(s, p)] for s in
                                                       ("github", "hf_model", "hf_dataset", "paper")}}
                    for p, w in windows.items()},
        "status": status,
        "fields": {"order": fields["order"], "labels": fields["labels"]},
        "tags": {"medical_ai": "의료 AI", "korean": "한국어", "on_device": "경량·온디바이스"},
        "lists": {f"{v}|{p}|{a}": [{k: e[k] for k in ("id", "score", "text", "flag", "fam") if e.get(k)}
                                   for e in lists[(v, p, a)]]
                  for v in VIEWS for p in PERIODS for a in AXES},
        "prev": {f"{v}|{p}|{a}": [[i, *prev_meta.get(i, (None, []))] for i in prev_lists.get((v, p, a), [])]
                 for v in VIEWS for p in PERIODS for a in AXES},
        "items": meta,
        "history": history(con, top10),
        "trends": trends,
        "matrix": matrix,
    }
    dump_json(ROOT / "docs" / "data" / "dashboard.json", data)
    return len(meta)
