"""순위 급변·이슈 감지 → Gmail SMTP로 알림 메일 발송.

.env 에 GMAIL_USER, GMAIL_APP_PASSWORD(구글 계정 '앱 비밀번호' 16자리), ALERT_TO 를 둔다.
"""
import html
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.util import cfg, log

VIEW_KO = {"integrated": "통합", "github": "GitHub", "hf_model": "HF 모델", "hf_dataset": "HF 데이터셋",
           "paper": "논문"}
PERIOD_KO = {"weekly": "주간", "monthly": "월간", "yearly": "연간", "total": "총 누적"}
AXIS_KO = {"popularity": "인기", "influence": "영향력"}


def detect_events(lists, prev_lists, keywords, flags, items_meta, alerted_keywords=()):
    """알림 조건(config/alerts.yaml)에 해당하는 사건 목록."""
    c = cfg("alerts")
    ev = []
    for view in c["watch_views"]:
        for p in c["watch_periods"]:
            for a in c["watch_axes"]:
                cur = [e["id"] for e in lists.get((view, p, a), [])][:10]
                prev = prev_lists.get((view, p, a))
                if not prev or not cur:
                    continue  # 비교 기준(전일 순위)이 없으면 판단하지 않음
                prev_rank = {iid: r + 1 for r, iid in enumerate(prev)}
                tag = f"{VIEW_KO[view]} {PERIOD_KO[p]} {AXIS_KO[a]}"
                if c["new_number_one"] and prev and cur[0] != prev[0]:
                    was = prev_rank.get(cur[0])
                    ev.append({"kind": "1위 교체", "where": tag, "id": cur[0],
                               "detail": f"{f'{was}위' if was else '순위권 밖'} → 1위 · 이전 1위: "
                                         f"{items_meta.get(prev[0], {}).get('t', prev[0])}"})
                for r, iid in enumerate(cur, 1):
                    pr = prev_rank.get(iid)
                    if (pr is None or pr > 10) and r <= c["new_entry_top"]:
                        ev.append({"kind": "신규 진입", "where": tag, "id": iid,
                                   "detail": f"{'순위권 밖' if pr is None else f'{pr}위'} → {r}위"})
                    elif pr is not None and pr <= 10 and pr - r >= c["rank_jump"]:
                        ev.append({"kind": "급상승", "where": tag, "id": iid, "detail": f"{pr}위 → {r}위 (▲{pr - r})"})
    for k in keywords:
        if k["ratio"] >= c["keyword_ratio"] and k["n"] >= c["keyword_min_count"] and k["k"] not in alerted_keywords:
            ev.append({"kind": "급상승 키워드", "where": "트렌드", "id": None,
                       "detail": f"'{k['k']}' 최근 7일 {k['n']}건 (평소 대비 ×{k['ratio']})"})
    if c["anomaly"]:
        for iid in flags:
            ev.append({"kind": "검증 필요", "where": "이상치", "id": iid, "detail": "스타·좋아요 비정상 급증(동반 지표 변화 없음)"})
    # 같은 순위표의 같은 항목은 한 번만 (1위 교체 > 신규 진입 > 급상승 순으로 먼저 들어간 것 유지)
    seen, out = set(), []
    for e in ev:
        key = (e["id"], e["where"]) if e["id"] else (e["kind"], e["detail"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out[: c["max_items_per_mail"]]


def render(events, items_meta, summary, site_url, run_date):
    rows = []
    for e in events:
        m = items_meta.get(e["id"], {}) if e["id"] else {}
        name = html.escape(m.get("t", "")) if m else ""
        link = f'<a href="{html.escape(m["u"])}">{name}</a>' if m.get("u") else name
        desc = html.escape(m.get("d", "") or "")
        rows.append(f"<tr><td style='padding:6px 8px;border-bottom:1px solid #eee;white-space:nowrap'><b>{e['kind']}</b>"
                    f"<br><span style='color:#888;font-size:12px'>{e['where']}</span></td>"
                    f"<td style='padding:6px 8px;border-bottom:1px solid #eee'>{link}"
                    f"<div style='color:#555;font-size:13px'>{desc}</div>"
                    f"<div style='color:#0a6;font-size:13px'>{html.escape(e['detail'])}</div></td></tr>")
    summ = "".join(f"<li>{html.escape(s)}</li>" for s in summary)
    return f"""<div style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:680px">
<h2 style="margin:0 0 4px">AI Pulse 변화 감지 알림</h2>
<div style="color:#888;margin-bottom:12px">{run_date} 06:00 KST 업데이트 기준</div>
{f'<h3 style="margin:12px 0 4px">오늘의 3줄 요약</h3><ul>{summ}</ul>' if summ else ''}
<h3 style="margin:16px 0 4px">감지된 변화 {len(events)}건</h3>
<table style="border-collapse:collapse;width:100%">{''.join(rows)}</table>
<p style="margin-top:16px"><a href="{site_url}">대시보드에서 전체 순위 보기 →</a></p>
<p style="color:#aaa;font-size:12px">알림 조건은 config/alerts.yaml 에서 조정할 수 있습니다.</p></div>"""


def send(subject, html_body):
    user, pw, to = (os.environ.get(k) for k in ("GMAIL_USER", "GMAIL_APP_PASSWORD", "ALERT_TO"))
    if not (user and pw and to):
        log.warning("메일 설정(GMAIL_USER/GMAIL_APP_PASSWORD/ALERT_TO)이 .env에 없어 발송 생략")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, f"AI Pulse <{user}>", to
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    # 587+STARTTLS: 인증 실패 시 465(SSL)처럼 연결만 끊지 않고 535 코드를 돌려줘서 원인을 알 수 있다
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        try:
            s.login(user, pw.replace(" ", ""))
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
            raise RuntimeError("Gmail이 앱 비밀번호를 거부함(535) — Google 계정에서 앱 비밀번호를 새로 만들어 "
                               ".env의 GMAIL_APP_PASSWORD를 바꿔야 합니다") from e
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    log.info("알림 메일 발송 → %s", to)
    return True


def _chg(iid, rank, prev):
    if not prev:
        return ""
    pr = {x: i + 1 for i, x in enumerate(prev)}.get(iid)
    if pr is None or pr > 10:
        return "<b style='color:#6250d6'>NEW</b>"
    d = pr - rank
    return (f"<b style='color:#0f8a3c'>▲{d}</b>" if d > 0 else f"<b style='color:#c93b3b'>▼{-d}</b>" if d < 0
            else "<span style='color:#999'>–</span>")


SRC_KO = {"github": "GitHub", "hf_model": "HF 모델", "hf_dataset": "HF 데이터셋", "paper": "논문"}


def _table(title, entries, items, prev, labels, n):
    rows = []
    for r, e in enumerate(entries[:n], 1):
        m = items.get(e["id"], {})
        name = html.escape(m.get("t", e["id"]))
        tag = f"{SRC_KO.get(m.get('s'), '')} · {html.escape(labels.get(m.get('f'), '미분류'))}"
        desc = html.escape((m.get("d") or "")[:70])
        rows.append(
            f"<tr><td style='padding:6px 6px;border-bottom:1px solid #eee;text-align:center;font-weight:700;color:#555'>{r}</td>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #eee'><a href='{html.escape(m.get('u', '#'))}' "
            f"style='color:#111;font-weight:700;text-decoration:none'>{name}</a>"
            f"<div style='color:#888;font-size:12px'>{tag}</div>"
            f"{f'<div style=\"color:#555;font-size:13px\">{desc}</div>' if desc else ''}</td>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #eee;white-space:nowrap;font-size:13px;text-align:right'>"
            f"{html.escape(e.get('text', ''))}<br>{_chg(e['id'], r, prev)}</td></tr>")
    return (f"<h3 style='margin:20px 0 6px;font-size:16px'>{title}</h3>"
            f"<table style='border-collapse:collapse;width:100%'>{''.join(rows)}</table>")


def render_daily(D, lists, prev_lists, events, site_url):
    """매일 보내는 랭킹 요약 메일 — 대시보드 상단 내용을 메일로."""
    items, labels = D["items"], D["fields"]["labels"]
    T = D["trends"]
    P = lambda k: prev_lists.get(k, [])  # noqa: E731
    parts = [f"""<div style="font-family:-apple-system,'Apple SD Gothic Neo',sans-serif;max-width:700px;color:#111">
<h2 style="margin:0 0 4px">AI Pulse 일일 랭킹</h2>
<div style="color:#888;margin-bottom:10px">{D['run_date']} 업데이트 · 주간 = {html.escape(D['periods']['weekly']['label'])}
 · <a href="{site_url}">대시보드 열기</a></div>"""]
    if T.get("summary"):
        parts.append("<h3 style='margin:14px 0 4px;font-size:16px'>오늘의 3줄 요약</h3><ol style='margin:0;padding-left:20px'>"
                     + "".join(f"<li>{html.escape(s)}</li>" for s in T["summary"]) + "</ol>")
    if events:
        rows = []
        for e in events:
            m = items.get(e["id"], {}) if e["id"] else {}
            rows.append(f"<li><b>{e['kind']}</b> <span style='color:#888'>[{e['where']}]</span> "
                        f"{html.escape(m.get('t', ''))} — <span style='color:#0a6'>{html.escape(e['detail'])}</span></li>")
        parts.append(f"<div style='background:#fff6e0;border-radius:8px;padding:8px 12px;margin-top:14px'>"
                     f"<b>⚡ 감지된 변화 {len(events)}건</b><ul style='margin:6px 0;padding-left:18px'>{''.join(rows)}</ul></div>")
    parts.append(_table("통합 트렌드 주간 인기 TOP 10", lists[("integrated", "weekly", "popularity")], items,
                        P(("integrated", "weekly", "popularity")), labels, 10))
    parts.append(_table("GitHub 주간 인기 TOP 5", lists[("github", "weekly", "popularity")], items,
                        P(("github", "weekly", "popularity")), labels, 5))
    parts.append(_table("Hugging Face 모델 주간 인기 TOP 5", lists[("hf_model", "weekly", "popularity")], items,
                        P(("hf_model", "weekly", "popularity")), labels, 5))
    # 논문: 분야별 이번 주 인기 1위
    best = []
    for f in D["fields"]["order"]:
        for e in lists[("paper", "weekly", "popularity")]:
            if items.get(e["id"], {}).get("f") == f:
                best.append({**e, "_f": f})
                break
    rows = "".join(
        f"<tr><td style='padding:5px 6px;border-bottom:1px solid #eee;white-space:nowrap;color:#555;font-size:13px'>"
        f"{html.escape(labels[e['_f']])}</td><td style='padding:5px 6px;border-bottom:1px solid #eee'>"
        f"<a href='{html.escape(items[e['id']].get('u', '#'))}' style='color:#111;text-decoration:none;font-weight:600'>"
        f"{html.escape(items[e['id']]['t'])}</a></td><td style='padding:5px 6px;border-bottom:1px solid #eee;"
        f"white-space:nowrap;font-size:13px;text-align:right'>{html.escape(e['text'])}</td></tr>" for e in best)
    parts.append(f"<h3 style='margin:20px 0 6px;font-size:16px'>이번 주 논문 분야별 1위</h3>"
                 f"<table style='border-collapse:collapse;width:100%'>{rows}</table>")
    if T.get("keywords"):
        kws = " · ".join(f"{html.escape(k['k'])} <b style='color:#0f8a3c'>{'NEW' if k['new'] else '×' + str(k['ratio'])}</b>"
                         for k in T["keywords"][:10])
        parts.append(f"<h3 style='margin:20px 0 6px;font-size:16px'>급상승 키워드</h3><div style='font-size:14px'>{kws}</div>")
    parts.append(f"<p style='margin-top:22px'><a href='{site_url}'>대시보드에서 전체 순위·검색 보기 →</a></p>"
                 "<p style='color:#aaa;font-size:12px'>매일 06:00 업데이트 후 자동 발송 · 설정: config/alerts.yaml</p></div>")
    return "".join(parts)
