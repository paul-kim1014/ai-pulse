"""로컬 LLM(Ollama qwen3:8b) — 규칙으로 못 정한 항목의 분야 분류, 한국어 한 줄 설명, 3줄 요약.

qwen3는 추론 모델이라 think:false를 꼭 넣는다(안 끄면 <think> 생성에 시간을 낭비).
"""
import json
import re

import httpx

from src.util import cfg, log

FIELD_DESC = {
    "llm": "언어모델, 추론, RAG, 번역, 정보추출, 텍스트 임베딩",
    "vision": "이미지·영상 인식, 확산모델·이미지/영상 생성, 3D",
    "multimodal": "비전-언어 모델, 문서 이해, 멀티모달",
    "speech": "음성인식·합성, 오디오, 음악 생성",
    "agents": "강화학습, AI 에이전트·프레임워크, 로봇",
    "efficiency": "학습 이론, 최적화, 양자화, 추론 가속, ML 인프라·라이브러리",
}


def _chat(prompt, fmt_json=True, num_predict=600):
    c = cfg("sources")["llm"]
    body = {"model": c["model"], "messages": [{"role": "user", "content": prompt}], "stream": False,
            "think": False, "options": {"temperature": 0.2, "num_predict": num_predict}}
    if fmt_json:
        body["format"] = "json"
    r = httpx.post(f"{c['ollama_url']}/api/chat", json=body, timeout=180)
    r.raise_for_status()
    txt = r.json()["message"]["content"]
    return re.sub(r"<think>.*?</think>", "", txt, flags=re.S).strip()


def available():
    try:
        httpx.get(cfg("sources")["llm"]["ollama_url"] + "/api/tags", timeout=5).raise_for_status()
        return True
    except Exception:
        return False


def classify_batch(rows):
    """rows: items 행 목록 → {item_id: field}"""
    lines = "\n".join(f"{i + 1}. [{r['source']}] {r['title']} — {(r['description'] or '')[:200]}"
                      for i, r in enumerate(rows))
    cats = "\n".join(f"- {k}: {v}" for k, v in FIELD_DESC.items())
    prompt = (f"다음 AI 관련 항목을 아래 6개 분야 중 하나로 분류하라.\n{cats}\n\n{lines}\n\n"
              'JSON으로만 답하라: {"1": "llm", "2": "vision", ...}')
    try:
        out = json.loads(_chat(prompt))
    except Exception as e:
        log.warning("LLM 분류 실패: %s", e)
        return {}
    res = {}
    for i, r in enumerate(rows):
        v = str(out.get(str(i + 1), "")).strip().lower()
        if v in FIELD_DESC:
            res[r["item_id"]] = v
    return res


def describe_batch(rows):
    """한국어 한 줄 설명(40자 내외). 주어진 정보만 사용하도록 지시한다."""
    def info(r):
        ex = json.loads(r["extra"] or "{}")
        kws = ", ".join(json.loads(r["keywords"] or "[]")[:8])
        base = ex.get("ai_summary") or r["description"] or ""
        meta = ex.get("pipeline_tag") or ""
        return f"[{r['source']}] {r['title']} | {meta} | {kws} | {base[:350]}"
    lines = "\n".join(f"{i + 1}. {info(r)}" for i, r in enumerate(rows))
    prompt = ("다음 AI 저장소·모델·데이터셋·논문 각각을 한국어 한 문장(35자 이내, 명사형 종결)으로 설명하라. "
              "주어진 정보에 없는 내용은 지어내지 말고, 정보가 부족하면 이름과 태그로 유형만 설명하라. "
              "고유명사(모델명·제품명)는 영어 그대로 둔다.\n\n"
              f"{lines}\n\nJSON으로만 답하라: {{\"1\": \"...\", \"2\": \"...\"}}")
    try:
        out = json.loads(_chat(prompt, num_predict=900))
    except Exception as e:
        log.warning("LLM 설명 생성 실패: %s", e)
        return {}
    res = {}
    for i, r in enumerate(rows):
        v = str(out.get(str(i + 1), "")).strip()
        if 4 <= len(v) <= 80:
            res[r["item_id"]] = v
    return res


def daily_summary(top_items, keywords, shares):
    prompt = ("너는 AI 업계 동향 분석가다. 아래 오늘의 순위·급상승 키워드·분야 점유율 변화를 보고 "
              "한국어로 정확히 3줄 요약을 써라. 각 줄은 60자 이내, 구체적인 항목명을 포함하되 "
              "항목명·모델명은 영어 원문 그대로 쓰고(한글 음차 금지), "
              "데이터에 없는 사실은 쓰지 마라.\n\n"
              f"[통합 주간 인기 상위]\n{top_items}\n\n[급상승 키워드]\n{keywords}\n\n[분야 점유율 변화]\n{shares}\n\n"
              'JSON으로만 답하라: {"lines": ["...", "...", "..."]}')
    out = json.loads(_chat(prompt, num_predict=700))
    lines = [str(x).strip() for x in out.get("lines", []) if str(x).strip()]
    return lines[:3]
