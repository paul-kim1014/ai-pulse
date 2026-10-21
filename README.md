# AI Pulse — AI 트렌드·영향력 랭킹 대시보드

GitHub 저장소 · Hugging Face 모델/데이터셋 · AI 논문을 하나의 평가 체계(인기 / 영향력)로 통합해
**주간 · 월간(지난달) · 연간(1월~) · 총 누적** 순위를 매일 06:00 KST에 갱신합니다.

- 대시보드: https://paul-kim1014.github.io/ai-pulse/
- 상단: AI 분야 통합 트렌드 순위(연구 패밀리 단위) + 오늘의 3줄 요약·급상승 키워드·분야 점유율·2×2 매트릭스
- 하단: 소스별(GitHub · HF 모델 · HF 데이터셋 · 논문) 분야별 영향력 순위 — 논문은 대분류 6개 탭
- 알림: 순위 급변(1위 교체·신규 진입·급상승)·급상승 키워드·이상치 발생 시 Gmail로 메일 발송 (`config/alerts.yaml`)

## 구조
```
config/   sources.yaml(편입 조건) weights.yaml(가중치) fields.yaml(분야 매핑) overrides.yaml(수동 보정) alerts.yaml(알림 조건)
src/      collectors/ storage/ classify/ scoring/ trends/ publish/ notify/ pipeline.py
docs/     정적 웹페이지 (GitHub Pages) + data/dashboard.json
scripts/run_daily.sh   launchd/com.paulkim.ai-pulse.plist
```

## 실행
```bash
venv/bin/python -m src.pipeline                 # 전체 (수집→분류→점수→요약→JSON→메일)
venv/bin/python -m src.pipeline --skip-collect  # 저장된 데이터로 점수·화면만 다시
venv/bin/python -m pytest -q
```

## 메일 알림 설정
`.env`의 `GMAIL_APP_PASSWORD`에 Google 계정의 **앱 비밀번호**(2단계 인증 필요, 16자리)를 넣으면 됩니다.
