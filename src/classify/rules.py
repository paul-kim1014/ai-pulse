"""규칙 기반 1차 분류 — arXiv 카테고리 / HF pipeline_tag / GitHub 토픽·키워드 → 대분류 6개 + 관심 태그."""
import json
import re
from functools import lru_cache

from src.util import cfg


@lru_cache(maxsize=None)
def _kw_re(kw):
    return re.compile(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"s?(?![a-z0-9])")


def _hits(text, kws):
    return sum(1 for k in kws if _kw_re(k).search(text))


def keyword_field(text, keywords=()):
    """텍스트·토픽에서 분야 키워드 매칭 수가 가장 많은 분야. 동점이면 fields.yaml 순서(구체적인 분야 우선)."""
    f = cfg("fields")["keywords"]
    text = text.lower()
    kwtext = " ".join(k.lower().replace("-", " ") for k in keywords)
    best, best_score = None, 0
    for field, kws in f.items():
        score = _hits(text, kws) + 2 * _hits(kwtext, [k.replace("-", " ") for k in kws])
        if score > best_score:
            best, best_score = field, score
    return best, best_score


def classify(row):
    """items 행 → (field 또는 None, 근거). None이면 LLM 2차 분류 대상."""
    F = cfg("fields")
    extra = json.loads(row["extra"] or "{}")
    kws = json.loads(row["keywords"] or "[]")
    text = f"{row['title'] or ''} {row['description'] or ''}"
    src = row["source"]
    kf, ks = keyword_field(f"{row['title'] or ''} {extra.get('ai_summary') or ''}", kws)

    if src == "paper":
        cats = extra.get("cats") or []
        head = cats[:3]
        if "cs.CV" in head and "cs.CL" in head:
            return "multimodal", "arxiv"
        # 제목에 강한 신호가 있으면 카테고리보다 우선 (예: cs.LG의 강화학습·cs.CL의 음성)
        if kf in ("multimodal", "speech", "agents") and ks >= 2:
            return kf, "keyword"
        prim = cats[0] if cats else None
        mapped = F["arxiv"].get(prim)
        if prim in ("cs.LG", "stat.ML", "cs.AI") and kf and ks >= 2:
            return kf, "keyword"
        if mapped:
            return mapped, "arxiv"
        if prim == "cs.AI" and kf:
            return kf, "keyword"
        for c in cats[1:]:
            if F["arxiv"].get(c):
                return F["arxiv"][c], "arxiv"
        kf2, ks2 = keyword_field(text, kws)
        return (kf2, "keyword") if ks2 >= 2 else (None, "")

    if src in ("hf_model", "hf_dataset"):
        tag = extra.get("pipeline_tag")
        if tag in F["pipeline_tag"]:
            mapped = F["pipeline_tag"][tag]
            # 텍스트 생성 모델이라도 에이전트·음성 특화 표시가 강하면 그쪽으로
            if mapped == "llm" and kf in ("agents", "speech", "multimodal") and ks >= 2:
                return kf, "keyword"
            return mapped, "pipeline"
        for t in extra.get("task_categories") or []:
            if t in F["pipeline_tag"]:
                return F["pipeline_tag"][t], "pipeline"
        kf2, ks2 = keyword_field(f"{row['name'].replace('/', ' ').replace('-', ' ')} {text}", kws)
        return (kf2, "keyword") if ks2 >= 1 else (None, "")

    # github
    kf2, ks2 = keyword_field(f"{row['name'].replace('/', ' ').replace('-', ' ')} {text}", kws)
    return (kf2, "keyword") if ks2 >= 1 else (None, "")


def interest_tags(row):
    """관심 태그(필터 전용). 3자 이하 키워드는 오탐 방지를 위해 토픽·태그와만 정확히 비교."""
    tags_cfg = cfg("sources")["interest_tags"]
    kws = [k.lower() for k in json.loads(row["keywords"] or "[]")]
    text = f"{row['name'] or ''} {row['title'] or ''} {row['description'] or ''}".lower()
    out = []
    for tag, words in tags_cfg.items():
        for w in words:
            w = w.lower()
            if (len(w) <= 3 and w in kws) or (len(w) > 3 and _kw_re(w).search(text + " " + " ".join(kws))):
                out.append(tag)
                break
    return out
