"""정규화 — log(1+x) 변환 후 그룹 내 백분위(0~100). 동점은 평균 순위."""
import math


def percentiles(values):
    """values: {key: x 또는 None} → {key: pct}. None은 결측(제외).
    그룹 전체가 같은 값이면 변별력이 없으므로 결측으로 본다(가중치 재분배 대상)."""
    xs = {k: math.log1p(max(v, 0)) for k, v in values.items() if v is not None}
    if len(xs) < 2 or len(set(xs.values())) == 1:
        return {}
    order = sorted(xs.items(), key=lambda kv: kv[1])
    n = len(order)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and order[j + 1][1] == order[i][1]:
            j += 1
        pct = 100.0 * ((i + j) / 2 + 0.5) / n
        for k in range(i, j + 1):
            out[order[k][0]] = pct
        i = j + 1
    return out


def weighted(pcts_by_metric, weights, key):
    """Σ w_i·pct_i — 값이 없는 지표의 가중치는 나머지에 비례 재분배."""
    num = den = 0.0
    for m, w in weights.items():
        p = pcts_by_metric.get(m, {}).get(key)
        if p is not None:
            num += w * p
            den += w
    return (num / den if den else None), den
