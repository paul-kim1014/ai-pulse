"""조작·왜곡 방어 — 기획서 3.4."""
import statistics
from collections import defaultdict
from datetime import timedelta

from src.util import cfg


def _increments(series):
    vals = [v for _, v in series]
    return [b - a for a, b in zip(vals, vals[1:])]


def detect(snaps, items, today):
    """가짜 스타·좋아요: 오늘 증가량이 최근 28일 평균 대비 z≥4 이면서 동반 지표(포크·이슈 / 다운로드) 변화 없음."""
    c = cfg("weights")["anomaly"]
    flags = set()
    for iid, it in items.items():
        if it["source"] == "paper":
            continue
        main, side = (("stars", ["forks", "issues_prs"]) if it["source"] == "github"
                      else ("likes", ["downloads_30d"]))
        inc = _increments(snaps.series(iid, main))
        if len(inc) < c["min_history_days"]:
            continue
        hist, last = inc[-29:-1], inc[-1]
        sd = statistics.pstdev(hist) or 1.0
        z = (last - statistics.mean(hist)) / sd
        if z < c["zscore_threshold"] or last < 50:
            continue
        side_moved = False
        for m in side:
            si = _increments(snaps.series(iid, m))
            if len(si) >= 8:
                sh = si[-29:-1]
                if si[-1] > statistics.mean(sh) + 2 * (statistics.pstdev(sh) or 1):
                    side_moved = True
        if not side_moved:
            flags.add(iid)
    return flags


def download_inflation(raws, items, bottom_pct):
    """자동 다운로드 부풀림: 다운로드 대비 좋아요·파생 모델 비율이 같은 분야 하위 1% → 다운로드 지표 제외."""
    for (s, p), rm in raws.items():
        if s not in ("hf_model", "hf_dataset"):
            continue
        by_field = defaultdict(list)
        for i, m in rm.items():
            dl = m["popularity"].get("downloads_30d") or 0
            if dl >= 10000:
                ratio = ((m["influence"].get("likes_total") or 0) + (m["influence"].get("derivatives") or 0)) / dl
                by_field[items[i]["field"]].append((ratio, i))
        for lst in by_field.values():
            if len(lst) < 100:
                continue
            lst.sort()
            for _, i in lst[: max(1, int(len(lst) * bottom_pct / 100))]:
                rm[i]["popularity"]["downloads_30d"] = None
                rm[i]["influence"]["family_downloads"] = None
                rm[i]["dl_flag"] = True
