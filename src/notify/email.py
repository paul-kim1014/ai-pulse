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


def detect_events(lists, prev_lists, keywords, flags, items_meta):
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
                    ev.append({"kind": "1위 교체", "where": tag, "id": cur[0],
                               "detail": f"이전 1위: {items_meta.get(prev[0], {}).get('t', prev[0])}"})
                for r, iid in enumerate(cur, 1):
                    pr = prev_rank.get(iid)
                    if (pr is None or pr > 10) and r <= c["new_entry_top"]:
                        ev.append({"kind": "신규 진입", "where": tag, "id": iid,
                                   "detail": f"{'순위권 밖' if pr is None else f'{pr}위'} → {r}위"})
                    elif pr is not None and pr <= 10 and pr - r >= c["rank_jump"]:
                        ev.append({"kind": "급상승", "where": tag, "id": iid, "detail": f"{pr}위 → {r}위 (▲{pr - r})"})
    for k in keywords:
        if k["ratio"] >= c["keyword_ratio"] and k["n"] >= c["keyword_min_count"]:
            ev.append({"kind": "급상승 키워드", "where": "트렌드", "id": None,
                       "detail": f"'{k['k']}' 최근 7일 {k['n']}건 (평소 대비 ×{k['ratio']})"})
    if c["anomaly"]:
        for iid in flags:
            ev.append({"kind": "검증 필요", "where": "이상치", "id": iid, "detail": "스타·좋아요 비정상 급증(동반 지표 변화 없음)"})
    # 같은 항목·종류 중복 제거
    seen, out = set(), []
    for e in ev:
        key = (e["kind"], e["id"], e["detail"] if e["id"] is None else "")
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
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(user, pw.replace(" ", ""))
        s.sendmail(user, [a.strip() for a in to.split(",")], msg.as_string())
    log.info("알림 메일 발송 → %s", to)
    return True
