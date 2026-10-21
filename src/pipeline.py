"""전체 실행 진입점 — 기획서 8.1 순서. 소스별 실패는 격리하고 runs 테이블에 남긴다.

사용: venv/bin/python -m src.pipeline [--date YYYY-MM-DD] [--skip-collect] [--no-llm] [--no-email]
"""
import argparse
import json
import os
import time
import traceback
from datetime import date, datetime, timedelta

from src.classify import llm
from src.classify.run import classify_all, describe_all
from src.collectors import github, huggingface, papers
from src.notify import email as mail
from src.publish.export import displayed_ids, export
from src.scoring.rank import rank_all
from src.scoring.score import AXES, PERIODS, compute
from src.storage.db import connect, log_run
from src.trends.keywords import field_share, rising_keywords
from src.util import KST, cfg, load_env, log, setup_logging, today_kst

SITE_URL = "https://paul-kim1014.github.io/ai-pulse/"


def step(con, status, name, fn, *args):
    t0 = datetime.now(KST)
    try:
        n = fn(*args)
        cnt = n[0] if isinstance(n, tuple) else (n or 0)
        status[name] = {"ok": True, "items": cnt, "sec": round((datetime.now(KST) - t0).total_seconds())}
        log.info("✓ %s: %s (%ds)", name, n, status[name]["sec"])
        log_run(con, name, t0.isoformat(), datetime.now(KST).isoformat(), "ok", cnt)
    except Exception as e:
        status[name] = {"ok": False, "error": str(e)[:200]}
        log.error("✗ %s 실패: %s\n%s", name, e, traceback.format_exc())
        log_run(con, name, t0.isoformat(), datetime.now(KST).isoformat(), "error", 0, str(e)[:500])
    con.commit()
    return status[name]["ok"]


def prev_lists(con, today):
    out = {}
    for row in con.execute("""SELECT view, period, axis, MAX(date) d FROM scores WHERE date<?
                              GROUP BY view, period, axis""", (today.isoformat(),)).fetchall():
        ids = [r[0] for r in con.execute("""SELECT item_id FROM scores WHERE view=? AND period=? AND axis=? AND date=?
                                             ORDER BY rank""", (row["view"], row["period"], row["axis"], row["d"]))]
        out[(row["view"], row["period"], row["axis"])] = ids
    return out


def store_scores(con, today, lists, prev, windows, items):
    d = today.isoformat()
    con.execute("DELETE FROM scores WHERE date=?", (d,))
    for (v, p, a), lst in lists.items():
        pr = {iid: r + 1 for r, iid in enumerate(prev.get((v, p, a), []))}
        con.executemany("INSERT INTO scores VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
            (v, e["id"], d, p, a, e["score"], r + 1, (pr[e["id"]] - (r + 1)) if e["id"] in pr else None,
             items[e["id"]]["field"], int(e["flag"]), windows[p]["label"]) for r, e in enumerate(lst)])
    con.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="today")
    ap.add_argument("--skip-collect", action="store_true", help="수집 생략, 저장된 스냅샷으로 점수·배포만")
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--no-email", action="store_true")
    ap.add_argument("--first-run", action="store_true", help="초기 백필(arXiv 카테고리 전체 등)")
    args = ap.parse_args()
    setup_logging()
    load_env()
    today = today_kst() if args.date == "today" else date.fromisoformat(args.date)
    t_start = time.time()
    con = connect()
    status = {}
    log.info("===== AI Pulse %s 실행 =====", today)

    if not args.skip_collect:
        # 1~3. 후보 발견·지표 수집·스냅샷 저장 (소스별 격리)
        step(con, status, "hf_papers", papers.collect_hf_daily, con, today)
        step(con, status, "s2_classics", papers.collect_s2_classics, con, today)
        step(con, status, "arxiv", papers.collect_arxiv_categories, con, today, 40000 if args.first_run else 3000)
        step(con, status, "github_discover", lambda: github.discover(con, today, papers.paper_repos(con)))
        step(con, status, "github", github.collect, con, today)
        step(con, status, "huggingface", huggingface.discover_and_collect, con, today)
        step(con, status, "hf_detail", huggingface.collect_details, con, today)
        step(con, status, "semantic_scholar", papers.collect_s2_citations, con, today)

    use_llm = not args.no_llm and llm.available()
    # 4. 분야 분류 (신규만)
    step(con, status, "classify", classify_all, con, use_llm)

    # 5. 점수·순위
    results, windows, modes, items, snaps, flags = compute(con, today)
    lists, fam, members = rank_all(results, windows, modes, items, snaps, today)
    prev = prev_lists(con, today)
    store_scores(con, today, lists, prev, windows, items)
    log.info("점수 산출 완료: 모드 %s", {f"{s}/{p}": m for (s, p), m in modes.items() if p != "total"})

    # 6. 트렌드·요약
    kws = rising_keywords(con, today)
    con.executemany("INSERT OR REPLACE INTO trends VALUES(?,?,?,?,?)",
                    [(today.isoformat(), k["k"], k["src"], k["n"], k["ratio"]) for k in kws])
    share = field_share(con, today, cfg("fields")["order"])
    wk = lists[("integrated", "weekly", "popularity")][:10]
    prev_wk = set(prev.get(("integrated", "weekly", "popularity"), [])[:10])
    new_entries = [e["id"] for e in wk if prev_wk and e["id"] not in prev_wk]
    if use_llm:
        step(con, status, "describe", describe_all, con, displayed_ids(lists, items))
    summary = []
    if use_llm:
        try:
            names = [con.execute("SELECT title, source, desc_ko FROM items WHERE item_id=?", (e["id"],)).fetchone()
                     for e in wk[:8]]
            top_txt = "\n".join(f"{i + 1}. {n['title']} ({n['source']}) {n['desc_ko'] or ''} {e['text']}"
                                for i, (n, e) in enumerate(zip(names, wk)))
            kw_txt = ", ".join(f"{k['k']}(×{k['ratio']})" for k in kws[:8]) or "없음"
            labels = cfg("fields")["labels"]
            sh_txt = ", ".join(f"{labels[f]} {v[-2]}%(4주 전 {v[-6]}%)" for f, v in share["share"].items())
            summary = llm.daily_summary(top_txt, kw_txt, sh_txt)
            con.execute("INSERT OR REPLACE INTO summaries VALUES(?,?,?)",
                        (today.isoformat(), json.dumps(summary, ensure_ascii=False), cfg("sources")["llm"]["model"]))
        except Exception as e:
            log.warning("3줄 요약 생략: %s", e)
    con.commit()

    trends = {"summary": summary, "keywords": kws, "share": share, "new_entries": new_entries,
              "flags": sorted(flags)}
    status["_elapsed_min"] = round((time.time() - t_start) / 60, 1)
    # 7. 배포용 JSON (git push는 scripts/run_daily.sh가 담당)
    n = export(con, today, lists, prev, windows, modes, items, snaps, trends, status)
    log.info("dashboard.json 생성: 항목 %d개", n)

    # 8. 알림 — 순위 급변·이슈 감지 시에만 메일
    if cfg("alerts").get("enabled") and not args.no_email:
        try:
            meta = json.load(open(os.path.join(os.path.dirname(__file__), "..", "docs", "data", "dashboard.json"),
                                  encoding="utf-8"))["items"]
            # 최근 3일 안에 이미 알린 급상승 키워드는 다시 알리지 않음
            since = (today - timedelta(days=3)).isoformat()
            alerted = {r[0][6:] for r in con.execute("SELECT keyword FROM trends WHERE date>=? AND date<? AND source='alerted'",
                                                 (since, today.isoformat()))}
            events = mail.detect_events(lists, prev, kws, flags, meta, alerted)
            if events:
                kinds = sorted({e["kind"] for e in events})
                subj = f"[AI Pulse] {today:%m/%d} 변화 감지 {len(events)}건 — {', '.join(kinds)}"
                if mail.send(subj, mail.render(events, meta, summary, SITE_URL, today.isoformat())):
                    for e in events:
                        if e["kind"] == "급상승 키워드":
                            k = e["detail"].split("'")[1]
                            con.execute("INSERT OR REPLACE INTO trends VALUES(?,?,?,?,?)",
                                        (today.isoformat(), f"alert:{k}", "alerted", 0, 0))
                    con.commit()
            else:
                log.info("알림 조건에 해당하는 변화 없음 → 메일 생략")
        except Exception as e:
            log.error("알림 단계 실패: %s", e)
    log.info("===== 완료 (%.1f분) =====", (time.time() - t_start) / 60)


if __name__ == "__main__":
    main()
