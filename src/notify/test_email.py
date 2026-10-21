"""메일 설정 확인용: 현재 대시보드 상위 항목으로 테스트 알림 1통을 보낸다.
사용: venv/bin/python -m src.notify.test_email
"""
import json

from src.notify import email as mail
from src.pipeline import SITE_URL
from src.util import ROOT, load_env, setup_logging

if __name__ == "__main__":
    setup_logging()
    load_env()
    D = json.load(open(ROOT / "docs" / "data" / "dashboard.json", encoding="utf-8"))
    top = D["lists"]["integrated|weekly|popularity"][:3]
    events = [{"kind": "테스트", "where": "통합 주간 인기", "id": e["id"], "detail": f"현재 {i + 1}위 · {e['text']}"}
              for i, e in enumerate(top)]
    ok = mail.send("[AI Pulse] 메일 알림 테스트", mail.render(events, D["items"], D["trends"]["summary"],
                                                          SITE_URL, D["run_date"]))
    print("발송 성공" if ok else "발송 안 됨 — .env의 GMAIL_APP_PASSWORD를 확인하세요")
