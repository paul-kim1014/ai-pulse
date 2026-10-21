"""트렌드 분석 — 급상승 키워드(최근 7일 ÷ 직전 28일 주평균), 분야 점유율(최근 12주)."""
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date, timedelta

STOP = {"llm", "llms", "ai", "machine-learning", "machine learning", "deep-learning", "deep learning",
        "artificial-intelligence", "python", "pytorch", "transformers", "safetensors", "large language models",
        "large-language-models", "language model", "language models", "neural networks", "dataset", "benchmark",
        "text-generation", "conversational", "model", "models", "generative-ai", "openai", "chatgpt",
        "tensorflow", "nlp", "deep neural networks", "gguf", "lora", "peft", "mlx", "trl", "sft",
        "evaluation", "en", "zh", "base_model", "fine-tuning", "finetune", "fine-tuned", "awesome",
        "awesome-list", "javascript", "typescript", "rust", "go", "react", "nodejs", "api", "docker",
        "image", "text", "video", "audio", "llama-cpp", "unsloth", "vllm", "chat", "instruct", "merge",
        "mergekit", "quantized", "4-bit", "8-bit", "text-generation-inference", "endpoints_compatible",
        "reinforcement learning", "computer-vision", "generation"}


STOP_KEYS = {re.sub(r"[^a-z0-9]", "", s) for s in STOP}


def _norm(k):
    k = k.lower().strip().replace("_", "-")
    k = re.sub(r"\s+", " ", k)
    return k


def _when(row):
    ex = json.loads(row["extra"] or "{}")
    d = ex.get("daily_date") or row["created_at"]
    try:
        return date.fromisoformat(d[:10]) if d else None
    except ValueError:
        return None


def rising_keywords(con, today, top=10, min_count=5, min_ratio=2.0):
    recent, prev = Counter(), Counter()
    src_of = defaultdict(Counter)
    r0, r1 = today - timedelta(days=7), today - timedelta(days=1)
    p0, p1 = today - timedelta(days=35), today - timedelta(days=8)
    shown = defaultdict(Counter)  # 같은 키워드의 표기 변형(deepseek-v4.1 / deepseek-v41 / DeepSeek V4.1)을 하나로
    for row in con.execute("SELECT source, keywords, extra, created_at FROM items WHERE keywords IS NOT NULL "
                           "AND created_at>=?", (p0.isoformat(),)):
        d = _when(row)
        if not d:
            continue
        kws = set()
        for raw in json.loads(row["keywords"] or "[]"):
            k = _norm(raw)
            if 2 < len(k) <= 40 and k not in STOP and ":" not in k:
                key = re.sub(r"[^a-z0-9가-힣]", "", k)
                if key and key not in STOP_KEYS:
                    kws.add(key)
                    shown[key][k] += 1
        if r0 <= d <= r1:
            recent.update(kws)
            for k in kws:
                src_of[k][row["source"]] += 1
        elif p0 <= d <= p1:
            prev.update(kws)
    out = []
    for k, n in recent.items():
        if n < min_count:
            continue
        base = prev.get(k, 0) / 4
        ratio = n / max(base, 1.0)
        if ratio >= min_ratio:
            out.append({"k": shown[k].most_common(1)[0][0], "n": n, "prev": round(base, 1), "ratio": round(ratio, 1),
                        "new": prev.get(k, 0) == 0, "src": src_of[k].most_common(1)[0][0]})
    out.sort(key=lambda x: (-x["ratio"] * math.log1p(x["n"]), -x["n"]))
    return out[:top]


def field_share(con, today, field_order, weeks=12):
    """최근 12주 분야별 인기 비중 — 그 주에 공개된 항목의 주목도(log 업보트·스타·좋아요) 합계 기준."""
    monday = today - timedelta(days=today.weekday())
    starts = [monday - timedelta(weeks=w) for w in range(weeks - 1, -1, -1)]
    sums = {f: [0.0] * weeks for f in field_order}
    metric = {"paper": "hf_upvotes", "github": "stars", "hf_model": "likes", "hf_dataset": "likes"}
    latest = {}
    for r in con.execute("""SELECT item_id, metric, value FROM snapshots WHERE date=(SELECT MAX(date) FROM snapshots)
                            AND metric IN ('hf_upvotes','stars','likes')"""):
        latest[(r[0], r[1])] = r[2]
    for row in con.execute("SELECT item_id, source, field, extra, created_at FROM items WHERE field IS NOT NULL "
                           "AND created_at>=?", (starts[0].isoformat(),)):
        d = _when(row)
        if not d or d < starts[0] or row["field"] not in sums:
            continue
        w = min((d - starts[0]).days // 7, weeks - 1)
        v = latest.get((row["item_id"], metric[row["source"]]))
        if v:
            sums[row["field"]][w] += math.log1p(v)
    totals = [sum(sums[f][w] for f in field_order) or 1 for w in range(weeks)]
    share = {f: [round(100 * sums[f][w] / totals[w], 1) for w in range(weeks)] for f in field_order}
    return {"weeks": [s.strftime("%m/%d") for s in starts], "share": share,
            "note": "이번 주는 진행 중(부분 집계)"}
