#!/bin/bash
# 매일 06:00 KST launchd(com.paulkim.ai-pulse)가 실행: 수집·평가 → dashboard.json → git push(GitHub Pages) → 이상 시 메일.
# launchd는 PATH가 최소한이므로 절대 경로를 쓴다.
set -o pipefail
cd "$HOME/ai-pulse" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
PY="$HOME/ai-pulse/venv/bin/python"
LOG="$HOME/ai-pulse/data/cron.log"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 일일 실행 시작 =====" >> "$LOG"

# 로컬 LLM(분류·한국어 설명·3줄 요약) — 떠 있지 않으면 기동
if ! curl -s -o /dev/null http://localhost:11434/api/tags; then
  echo "Ollama 미응답 → 기동" >> "$LOG"
  /usr/local/bin/ollama serve >> "$LOG" 2>&1 &
  sleep 8
fi

"$PY" -u -m src.pipeline >> "$LOG" 2>&1
STATUS=$?

if [ -f docs/data/dashboard.json ]; then
  /usr/bin/git add docs >> "$LOG" 2>&1
  if ! /usr/bin/git diff --cached --quiet; then
    /usr/bin/git commit -q -m "auto: 일일 업데이트 $(date '+%Y-%m-%d')" >> "$LOG" 2>&1
    /usr/bin/git push -q origin main >> "$LOG" 2>&1 && echo "push 완료" >> "$LOG" || echo "push 실패" >> "$LOG"
  fi
fi

# 주 1회(일요일) DB 백업 — 최근 4개 보관
if [ "$(date +%u)" = "7" ]; then
  mkdir -p data/backup && cp data/ai_pulse.db "data/backup/ai_pulse_$(date +%Y%m%d).db"
  ls -1t data/backup/*.db | tail -n +5 | xargs -I{} rm -f {}
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 종료 (exit $STATUS) =====" >> "$LOG"
exit $STATUS
