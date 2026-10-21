"""분류 단계: 신규·미분류 항목만 규칙 → LLM 순으로 분류하고 관심 태그를 붙인다 (결과는 캐시)."""
import json

from src.classify import llm, rules
from src.util import cfg, log


def classify_all(con, use_llm=True):
    ov = cfg("overrides") or {}
    rows = con.execute("SELECT * FROM items WHERE field IS NULL OR field_by IN ('', 'pending')").fetchall()
    pending = []
    for r in rows:
        f, by = rules.classify(r)
        if f:
            con.execute("UPDATE items SET field=?, field_by=? WHERE item_id=?", (f, f"rule-{by}", r["item_id"]))
        else:
            pending.append(r)
    # 카테고리가 나중에 들어온 논문은 키워드 분류 → arXiv 분류로 갱신
    for r in con.execute("""SELECT * FROM items WHERE source='paper' AND field_by IN ('rule-keyword','llm')
                            AND json_array_length(json_extract(extra,'$.cats'))>0""").fetchall():
        f, by = rules.classify(r)
        if f and by == "arxiv":
            con.execute("UPDATE items SET field=?, field_by='rule-arxiv' WHERE item_id=?", (f, r["item_id"]))
    n_llm = 0
    if pending and use_llm and llm.available():
        cap = cfg("sources")["llm"]["classify_per_run"]
        for i in range(0, min(len(pending), cap), 10):
            res = llm.classify_batch(pending[i:i + 10])
            for iid, f in res.items():
                con.execute("UPDATE items SET field=?, field_by='llm' WHERE item_id=?", (f, iid))
                n_llm += 1
            con.commit()
    # 관심 태그는 설명이 바뀔 수 있어 매번 갱신 (가벼움)
    for r in con.execute("SELECT item_id, name, title, description, keywords FROM items"):
        con.execute("UPDATE items SET tags=? WHERE item_id=?", (json.dumps(rules.interest_tags(r)), r["item_id"]))
    for iid, o in ov.items():
        if "field" in o:
            con.execute("UPDATE items SET field=?, field_by='override' WHERE item_id=?", (o["field"], iid))
        if "tags" in o:
            con.execute("UPDATE items SET tags=? WHERE item_id=?", (json.dumps(o["tags"]), iid))
        if o.get("exclude"):
            con.execute("UPDATE items SET is_active=0 WHERE item_id=?", (iid,))
    con.commit()
    left = con.execute("SELECT COUNT(*) FROM items WHERE field IS NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    log.info("분류: 규칙 %d, LLM %d, 미분류 %d/%d (%.1f%%)", len(rows) - len(pending), n_llm, left, total,
             100 * left / max(total, 1))
    return left, total


def describe_all(con, item_ids):
    """화면에 노출되는 항목 중 한국어 설명이 없는 것만 생성 (캐시)."""
    if not item_ids or not llm.available():
        return 0
    cap = cfg("sources")["llm"]["describe_per_run"]
    qmarks = ",".join("?" * len(item_ids))
    rows = con.execute(f"SELECT * FROM items WHERE desc_ko IS NULL AND item_id IN ({qmarks})",
                       list(item_ids)).fetchall()
    order = {iid: i for i, iid in enumerate(item_ids)}  # 우선순위(상위 노출 순)대로
    rows = sorted(rows, key=lambda r: order.get(r["item_id"], 1e9))[:cap]
    n = 0
    for i in range(0, len(rows), 8):
        for iid, d in llm.describe_batch(rows[i:i + 8]).items():
            con.execute("UPDATE items SET desc_ko=? WHERE item_id=?", (d, iid))
            n += 1
        con.commit()
        if i and i % 200 == 0:
            log.info("한국어 설명 %d/%d", n, len(rows))
    return n
