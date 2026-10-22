"""Hugging Face Hub 수집기 — 모델·데이터셋 목록(정렬 상위) + 상세(파생 모델·Spaces) 순환 조회."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from src.storage.db import put_metrics, upsert_item
from src.util import cfg, http_get, log

API = "https://huggingface.co/api"
ARXIV_TAG = re.compile(r"^arxiv:(\d{4}\.\d{4,5})$")
SYSTEM_TAG = re.compile(r"^(license:|region:|base_model:|dataset:|arxiv:|doi:|endpoints_|autotrain|"
                        r"text-generation-inference|deploy:|custom_code|safetensors|transformers|pytorch|"
                        r"diffusers|gguf$|onnx|tf$|jax$|has_space|format:|modality:|library:|size_categories:|"
                        r"task_ids:|language:|en$|\d+-bit|fp8|bf16)")


def _headers():
    tok = os.environ.get("HF_TOKEN")
    return {"Authorization": f"Bearer {tok}"} if tok else None


def _list(kind, sort, limit):
    exp = ["likes", "downloads", "trendingScore", "tags", "createdAt"]
    exp += ["pipeline_tag", "library_name"] if kind == "models" else ["description"]
    params = [("sort", sort), ("direction", "-1"), ("limit", str(limit))] + [("expand[]", e) for e in exp]
    return http_get(f"{API}/{kind}", params=params, headers=_headers()).json()


def _arxiv_links(tags):
    ids = [m.group(1) for t in tags if (m := ARXIV_TAG.match(t))]
    return ids


def discover_and_collect(con, today):
    """정렬 상위 목록을 받아 편입 + 목록에 포함된 지표(좋아요·30일 다운로드·trendingScore)를 기록."""
    c = cfg("sources")["huggingface"]
    day = today.isoformat()
    seen = {}
    for kind, tops in (("models", c["models_top_n"]), ("datasets", c["datasets_top_n"])):
        for sort, n in tops.items():
            try:
                for m in _list(kind, sort, n):
                    seen[(kind, m["id"])] = m
            except Exception as e:
                log.warning("HF %s 목록(%s) 실패: %s", kind, sort, e)
    # 이미 추적 중인데 오늘 목록에 없는 항목도 계속 추적해야 하므로, 목록 밖 항목은 상세 조회 때 보충
    for (kind, mid), m in seen.items():
        src = "hf_model" if kind == "models" else "hf_dataset"
        tags = m.get("tags") or []
        arx = _arxiv_links(tags)
        kw = [t for t in tags if not SYSTEM_TAG.match(t)]
        url = f"https://huggingface.co/{mid}" if kind == "models" else f"https://huggingface.co/datasets/{mid}"
        upsert_item(con, {
            "item_id": f"{src}:{mid}", "source": src, "type": kind[:-1], "name": mid, "title": mid, "url": url,
            "description": (m.get("description") or "")[:500] or None,
            "created_at": (m.get("createdAt") or "")[:10] or None, "keywords": kw,
            "arxiv_id": arx[0] if 0 < len(arx) <= 2 else None,
            "extra": {"pipeline_tag": m.get("pipeline_tag"), "library": m.get("library_name"),
                      "arxiv_all": arx[:5],
                      "task_categories": [t.split(":", 1)[1] for t in tags if t.startswith("task_categories:")]},
        }, day)
        put_metrics(con, f"{src}:{mid}", day, {
            "likes": m.get("likes"), "downloads_30d": m.get("downloads"), "trending_score": m.get("trendingScore"),
        })
    con.commit()
    return len(seen)


def _detail(item_id):
    src, mid = item_id.split(":", 1)
    out = {}
    if src == "hf_model":
        info = http_get(f"{API}/models/{mid}", params=[("expand[]", "spaces"), ("expand[]", "downloadsAllTime"),
                                                        ("expand[]", "likes"), ("expand[]", "downloads"),
                                                        ("expand[]", "trendingScore")],
                        headers=_headers()).json()
        out["spaces"] = len(info.get("spaces") or [])
        out["downloads_all"] = info.get("downloadsAllTime")
        out["likes"] = info.get("likes")
        out["downloads_30d"] = info.get("downloads")
        out["trending_score"] = info.get("trendingScore")
        filt = f"base_model:{mid}"
    else:
        info = http_get(f"{API}/datasets/{mid}", params=[("expand[]", "downloadsAllTime"), ("expand[]", "likes"),
                                                          ("expand[]", "downloads")], headers=_headers()).json()
        out["downloads_all"] = info.get("downloadsAllTime")
        out["likes"] = info.get("likes")
        out["downloads_30d"] = info.get("downloads")
        filt = f"dataset:{mid}"
    # 파생 모델(파인튜닝·양자화·어댑터·머지) — 데이터셋은 그 데이터로 학습한 모델
    der = http_get(f"{API}/models", params=[("filter", filt), ("limit", "1000"), ("expand[]", "downloads")],
                   headers=_headers()).json()
    out["derivatives"] = len(der)
    out["family_downloads"] = (out.get("downloads_30d") or 0) + sum(d.get("downloads") or 0 for d in der)
    return item_id, out


def collect_details(con, today):
    """파생 모델 수·Spaces 수·전체 다운로드 — 비싸므로 오래된 것부터 하루 상한만큼 순환 조회."""
    c = cfg("sources")["huggingface"]
    day = today.isoformat()
    stale = (today - timedelta(days=c["detail_refresh_days"])).isoformat()
    rows = con.execute(
        """SELECT i.item_id FROM items i
           LEFT JOIN snapshots s ON s.item_id=i.item_id AND s.metric='downloads_30d' AND s.date=?
           LEFT JOIN snapshots l ON l.item_id=i.item_id AND l.metric='likes' AND l.date=?
           WHERE i.source IN ('hf_model','hf_dataset') AND i.is_active=1
             AND (i.detail_at IS NULL OR i.detail_at<?)
           ORDER BY (i.detail_at IS NOT NULL), COALESCE(l.value,0)+COALESCE(s.value,0)/1000 DESC LIMIT ?""",
        (day, day, stale, c["detail_per_day"])).fetchall()
    ids = [r["item_id"] for r in rows]
    done = 0
    with ThreadPoolExecutor(6) as ex:
        for fut in [ex.submit(_detail, i) for i in ids]:
            try:
                iid, m = fut.result()
            except Exception as e:
                log.debug("HF 상세 실패: %s", e)
                continue
            put_metrics(con, iid, day, m)
            con.execute("UPDATE items SET detail_at=? WHERE item_id=?", (day, iid))
            done += 1
    con.commit()
    return done


def collect_watchlist(con, today):
    """관심 목록의 HF 모델·데이터셋 — 정렬 상위 목록에 없어도 매일 상세 지표를 조회."""
    wl = cfg("watchlist")
    ids = [f"hf_model:{m}" for m in wl.get("hf_model") or []] + [f"hf_dataset:{d}" for d in wl.get("hf_dataset") or []]
    n = 0
    for iid in ids:
        src, mid = iid.split(":", 1)
        url = f"https://huggingface.co/{mid}" if src == "hf_model" else f"https://huggingface.co/datasets/{mid}"
        con.execute("INSERT OR IGNORE INTO items(item_id, source, type, name, title, url, first_seen, last_seen)"
                    " VALUES(?,?,?,?,?,?,?,?)", (iid, src, src[3:], mid, mid, url, today.isoformat(), today.isoformat()))
        try:
            kind = "models" if src == "hf_model" else "datasets"
            m = http_get(f"{API}/{kind}/{mid}", params=[("expand[]", e) for e in
                         ("tags", "createdAt", "pipeline_tag" if kind == "models" else "description")],
                         headers=_headers()).json()
            tags = m.get("tags") or []
            arx = _arxiv_links(tags)
            upsert_item(con, {"item_id": iid, "source": src, "name": mid, "title": mid, "url": url,
                              "description": (m.get("description") or "")[:500] or None,
                              "created_at": (m.get("createdAt") or "")[:10] or None,
                              "keywords": [t for t in tags if not SYSTEM_TAG.match(t)],
                              "arxiv_id": arx[0] if 0 < len(arx) <= 2 else None,
                              "extra": {"pipeline_tag": m.get("pipeline_tag")}}, today.isoformat())
            _, met = _detail(iid)
            put_metrics(con, iid, today.isoformat(), met)
            con.execute("UPDATE items SET detail_at=?, is_active=1 WHERE item_id=?", (today.isoformat(), iid))
            n += 1
        except Exception as e:
            log.warning("관심 목록 HF 조회 실패 %s: %s", mid, e)
    con.commit()
    return n
