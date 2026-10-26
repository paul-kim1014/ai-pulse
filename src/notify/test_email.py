"""메일 설정 확인용: 실제 일일 랭킹 메일과 같은 형태로 1통을 보낸다 (최근 dashboard.json 기준).
사용: venv/bin/python -m src.notify.test_email          # 발송
      venv/bin/python -m src.notify.test_email --preview  # 발송 없이 data/mail_preview.html 로 저장
"""
import json
import sys

from src.notify import email as mail
from src.pipeline import SITE_URL
from src.util import ROOT, load_env, setup_logging

if __name__ == "__main__":
    setup_logging()
    load_env()
    D = json.load(open(ROOT / "docs" / "data" / "dashboard.json", encoding="utf-8"))
    lists = {tuple(k.split("|")): v for k, v in D["lists"].items()}
    prev = {tuple(k.split("|")): [x[0] for x in v] for k, v in D["prev"].items()}
    body = mail.render_daily(D, lists, prev, [], SITE_URL)
    if "--preview" in sys.argv:
        (ROOT / "data" / "mail_preview.html").write_text('<meta charset="utf-8">' + body, encoding="utf-8")
        print("미리보기 저장:", ROOT / "data" / "mail_preview.html")
    else:
        try:
            ok = mail.send(f"[AI Pulse] {D['run_date'][5:].replace('-', '/')} 일일 랭킹 (테스트 발송)", body)
            print("발송 성공" if ok else "발송 안 됨 — .env의 메일 설정을 확인하세요")
        except Exception as e:
            print("발송 실패:", e)
