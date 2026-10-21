# AI Pulse 프로젝트 지침
## 목적
GitHub·Hugging Face·AI 논문 데이터를 매일 수집해 분야별 인기·영향력 순위를 정적 웹페이지(GitHub Pages)로 배포하고,
순위 급변·이슈 발생 시 Gmail로 알림을 보낸다. 기획서: AI_트렌드_랭킹_시스템_개발기획서.docx

## 규칙
- Python 3.14 venv(`venv/`). 의존성은 requirements.txt에만 추가 (기획서의 uv 대신 기존 프로젝트와 같은 venv 사용)
- 설정값(가중치, 편입 조건, 분야 매핑, 알림 조건)은 하드코딩하지 말고 config/*.yaml에서 읽는다
- 비밀 정보는 .env에서 읽고 절대 커밋하지 않는다. GitHub는 `gh` CLI 로그인을 사용한다
- 웹 화면 스크래핑 금지. 공식 API만 사용한다
- 새 기능에는 pytest 테스트를 추가하고, 테스트는 실제 API를 호출하지 않는다
- 사용자는 파이썬 비전공자이므로 변경 후 무엇을 왜 바꿨는지 한국어로 짧게 설명한다

## 실행
- 전체: venv/bin/python -m src.pipeline            (--skip-collect: 수집 생략, --no-email, --no-llm)
- 테스트: venv/bin/python -m pytest -q
- 자동: launchd com.paulkim.ai-pulse → scripts/run_daily.sh (매일 06:00), 로그 data/cron.log

## 알려진 제약 (2026-10 기준)
- GitHub stargazer 목록(시점 포함)이 막혀 스타 이력 백필 불가 → 스냅샷 차이로만 증가량 계산.
  기준일 스냅샷이 없는 기간은 '기간 내 신규 공개 항목' 코호트로 평가(score.py delta_mode)
- GitHub 의존 저장소(Used by)·패키지 다운로드는 공식 API가 없어 결측 → 가중치 재분배
- OpenAlex는 키 필수·과금이라 미사용 (Semantic Scholar로 대체)
