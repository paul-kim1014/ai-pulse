"""논문 수집기 — HF Daily Papers(업보트·코드 연결), Semantic Scholar(인용), arXiv(카테고리).

논문은 '공개 시기 코호트' 방식으로 평가하므로 과거 Daily Papers를 한 번 백필하면
첫날부터 주간·월간·연간·누적 순위를 모두 만들 수 있다.
"""
import json
import re
import time
import zlib
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from src.storage.db import get_meta, put_metrics, set_meta, upsert_item
from src.util import cfg, http_get, http_post_json, log

HF = "https://huggingface.co/api/daily_papers"
S2 = "https://api.semanticscholar.org/graph/v1"
GH_REPO = re.compile(r"github\.com/([\w.-]+/[\w.-]+)")


def _daily(d):
    return d, http_get(HF, params={"date": d.isoformat(), "limit": "100"}).json()


def collect_hf_daily(con, today):
    """HF Daily Papers — 미수집 날짜 백필 + 최근 N일 재조회(업보트 변화 반영)."""
    c = cfg("sources")["papers"]
    done = set(json.loads(get_meta(con, "hf_daily_done", "[]")))
    start = date.fromisoformat(c["hf_backfill_start"])
    refresh_from = today - timedelta(days=c["hf_refresh_days"])
    # 06:00 KST는 UTC로 전날 21시 — KST '오늘' 날짜는 HF에 아직 없으므로(400) 어제까지만 조회
    days = [start + timedelta(days=i) for i in range((today - start).days)]
    todo = [d for d in days if d.isoformat() not in done or d >= refresh_from]
    log.info("HF Daily Papers %d일 조회 (백필 포함)", len(todo))
    n = 0
    with ThreadPoolExecutor(4) as ex:
        for fut in [ex.submit(_daily, d) for d in todo]:
            try:
                d, rows = fut.result()
            except Exception as e:
                log.warning("Daily Papers 조회 실패: %s", e)
                continue
            for row in rows:
                _store_hf_paper(con, row, d, today)
                n += 1
            if d < refresh_from:
                done.add(d.isoformat())
    set_meta(con, "hf_daily_done", json.dumps(sorted(done)))
    con.commit()
    return n


def _pub_date(aid, published, daily_date):
    """공개일 = HF publishedAt·Daily 등록일 중 이른 날. 단 arXiv ID(yymm)보다 2개월 넘게 늦으면
    재게재(학술지 버전 등) 날짜이므로 arXiv 최초 공개 월 1일로 본다."""
    cands = [d for d in ((published or "")[:10], daily_date.isoformat() if daily_date else "") if d]
    d = min(cands) if cands else None
    m = re.match(r"^(\d{2})(\d{2})\.", aid or "")
    if m:
        first = date(2000 + int(m.group(1)), int(m.group(2)), 1)
        if not d or date.fromisoformat(d) > first + timedelta(days=62):
            d = first.isoformat()
    return d


def _store_hf_paper(con, row, daily_date, today):
    p = row.get("paper") or {}
    aid = p.get("id")
    if not aid:
        return
    gh = p.get("githubRepo") or ""
    m = GH_REPO.search(gh)
    repo = m.group(1).removesuffix(".git") if m else None
    upsert_item(con, {
        "item_id": f"paper:{aid}", "source": "paper", "type": "paper", "name": aid,
        "title": (p.get("title") or row.get("title") or "").strip(),
        "url": f"https://arxiv.org/abs/{aid}",
        "description": (p.get("summary") or "")[:700],
        "created_at": _pub_date(aid, p.get("publishedAt"), daily_date),
        "keywords": p.get("ai_keywords") or [],
        "arxiv_id": aid,
        "extra": {"hf_url": f"https://huggingface.co/papers/{aid}", "daily_date": daily_date.isoformat(),
                  "ai_summary": (p.get("ai_summary") or "")[:400], "repo": repo,
                  "org": (p.get("organization") or {}).get("fullname")},
    }, today.isoformat())
    put_metrics(con, f"paper:{aid}", today.isoformat(), {
        "hf_upvotes": p.get("upvotes"), "comments": row.get("numComments"),
        "code_stars": p.get("githubStars") if repo else None,
    })


S2_FIELDS = "externalIds,citationCount,influentialCitationCount,venue,year,publicationDate"
CLASSIC_QUERIES = [
    '"language model" | transformer | "machine translation" | "question answering" | "retrieval augmented"',
    'diffusion | "image generation" | "object detection" | segmentation | "image recognition"',
    '"vision-language" | multimodal | "visual question answering" | "image captioning" | "contrastive language-image"',
    '"speech recognition" | "text-to-speech" | "speech synthesis" | audio | "speaker"',
    '"reinforcement learning" | agent | robot | "policy optimization" | "embodied"',
    'optimization | quantization | "knowledge distillation" | "batch normalization" | "graph neural network" | "neural architecture"',
]


def collect_s2_classics(con, today):
    """역대 고인용 AI 논문(총 누적 코호트용) — 주 1회만 갱신."""
    last = get_meta(con, "s2_classics_at")
    if last and (today - date.fromisoformat(last)).days < 7:
        return 0
    min_c = cfg("sources")["papers"]["s2_classic_min_citations"]
    n = 0
    for q in CLASSIC_QUERIES:
        try:
            data = http_get(f"{S2}/paper/search/bulk", params={
                "query": q, "sort": "citationCount:desc", "fieldsOfStudy": "Computer Science",
                "minCitationCount": str(min_c), "year": "2012-",
                "fields": "title,abstract,externalIds,year,publicationDate,citationCount,influentialCitationCount,venue,url",
            }).json()
        except Exception as e:
            log.warning("S2 고인용 검색 실패: %s", e)
            continue
        for p in (data.get("data") or [])[:400]:
            aid = (p.get("externalIds") or {}).get("ArXiv")
            iid = f"paper:{aid}" if aid else f"paper:s2:{p['paperId']}"
            upsert_item(con, {
                "item_id": iid, "source": "paper", "type": "paper", "name": aid or p["paperId"],
                "title": (p.get("title") or "").strip(),
                "url": f"https://arxiv.org/abs/{aid}" if aid else p.get("url"),
                "description": (p.get("abstract") or "")[:700] or None,
                "created_at": p.get("publicationDate") or (f"{p['year']}-06-30" if p.get("year") else None),
                "arxiv_id": aid, "extra": {"s2_id": p["paperId"]},
            }, today.isoformat())
            n += 1
        time.sleep(1.5)
    set_meta(con, "s2_classics_at", today.isoformat())
    con.commit()
    return n


def collect_s2_citations(con, today):
    """추적 논문 전체의 인용 수 — batch 500편 단위."""
    top_venues = [v.lower() for v in cfg("sources")["papers"]["top_venues"]]
    rows = con.execute("SELECT item_id, arxiv_id, extra, created_at FROM items WHERE source='paper' AND is_active=1"
                       ).fetchall()
    # 1년 넘은 논문은 인용이 천천히 변하므로 요일별로 나눠 주 1회만 갱신 (비인증 한도·시간 절약)
    old_cut = (today - timedelta(days=365)).isoformat()
    wd = today.weekday()
    rows = [r for r in rows if (r["created_at"] or "") >= old_cut or zlib.crc32(r["item_id"].encode()) % 7 == wd]
    keys = []
    for r in rows:
        ex = json.loads(r["extra"] or "{}")
        if r["arxiv_id"]:
            keys.append((r["item_id"], f"ARXIV:{r['arxiv_id']}"))
        elif ex.get("s2_id"):
            keys.append((r["item_id"], ex["s2_id"]))
    day = today.isoformat()
    n = 0
    chunks = [keys[i:i + 500] for i in range(0, len(keys), 500)]
    failed = []
    for attempt in range(3):  # 비인증 공유 한도(429)로 실패한 묶음은 쉬었다가 최대 2번 더 시도
        if attempt:
            if not failed:
                break
            log.info("S2 실패 묶음 %d개 재시도 (%d차)", len(failed), attempt)
            time.sleep(60)
            chunks, failed = failed, []
        n += _s2_round(con, chunks, failed, day, top_venues)
    if failed:
        log.warning("S2 최종 실패 %d묶음 — 해당 논문은 전일 인용 값 유지", len(failed))
    return n


def _s2_round(con, chunks, failed, day, top_venues):
    n = 0
    for chunk in chunks:
        try:
            res = http_post_json(f"{S2}/paper/batch", {"ids": [k for _, k in chunk]}, params={"fields": S2_FIELDS})
        except Exception as e:
            log.debug("S2 batch 실패: %s", e)
            failed.append(chunk)
            time.sleep(5)
            continue
        for (iid, _), p in zip(chunk, res):
            if not p:
                continue
            venue = (p.get("venue") or "").strip()
            put_metrics(con, iid, day, {"citations": p.get("citationCount"),
                                        "influential_citations": p.get("influentialCitationCount"),
                                        "venue": 1 if venue and any(v in venue.lower() for v in top_venues) else 0})
            if venue:
                con.execute("UPDATE items SET extra=json_set(COALESCE(extra,'{}'),'$.venue',?) WHERE item_id=?",
                            (venue, iid))
            n += 1
        con.commit()
        time.sleep(1.2)  # 비인증 공유 한도 보호
    return n


ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def collect_arxiv_categories(con, today, limit=6000):
    """분야 분류용 arXiv 카테고리 — 아직 없는 논문만 100편씩, 3초 간격."""
    rows = con.execute("""SELECT item_id, arxiv_id FROM items WHERE source='paper' AND arxiv_id IS NOT NULL
                          AND json_extract(COALESCE(extra,'{}'),'$.cats') IS NULL LIMIT ?""", (limit,)).fetchall()
    n = 0
    for i in range(0, len(rows), 100):
        chunk = rows[i:i + 100]
        try:
            xml = http_get("https://export.arxiv.org/api/query",
                           params={"id_list": ",".join(r["arxiv_id"] for r in chunk), "max_results": "100"}).text
            root = ET.fromstring(xml)
        except Exception as e:
            log.warning("arXiv 조회 실패: %s", e)
            time.sleep(10)
            continue
        got = {}
        for ent in root.findall("a:entry", ATOM):
            eid = (ent.findtext("a:id", "", ATOM) or "").rsplit("/", 1)[-1]
            eid = re.sub(r"v\d+$", "", eid)
            prim = ent.find("arxiv:primary_category", ATOM)
            cats = [c.get("term") for c in ent.findall("a:category", ATOM)]
            if prim is not None:
                cats = [prim.get("term")] + [c for c in cats if c != prim.get("term")]
            got[eid] = cats
        for r in chunk:
            cats = got.get(r["arxiv_id"], [])
            con.execute("UPDATE items SET extra=json_set(COALESCE(extra,'{}'),'$.cats',json(?)) WHERE item_id=?",
                        (json.dumps(cats), r["item_id"]))
            n += 1
        con.commit()
        time.sleep(3.1)
    return n


def paper_repos(con, min_stars=100):
    """논문에 연결된 공식 코드 저장소 중 스타 min_stars 이상 (GitHub 추적 대상에 편입)."""
    out = []
    for r in con.execute("""SELECT i.arxiv_id, json_extract(i.extra,'$.repo') repo, MAX(s.value) stars
                            FROM items i JOIN snapshots s ON s.item_id=i.item_id AND s.metric='code_stars'
                            WHERE i.source='paper' AND json_extract(i.extra,'$.repo') IS NOT NULL
                            GROUP BY i.item_id HAVING stars>=?""", (min_stars,)):
        out.append((r["repo"], r["arxiv_id"]))
    return out
