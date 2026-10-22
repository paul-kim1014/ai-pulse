"""관심 목록 관리 — 자동 수집에서 빠진 저장소·모델을 직접 추적 대상에 넣는다.

  venv/bin/python -m src.watch add storytold/artcraft              # GitHub 저장소
  venv/bin/python -m src.watch add hf_model:org/model              # HF 모델
  venv/bin/python -m src.watch add hf_dataset:org/dataset          # HF 데이터셋
  venv/bin/python -m src.watch list

추가하면 바로 지표를 한 번 수집하고 분류까지 해 둔다. 순위·검색 화면 반영은 다음 06:00 실행
(또는 `venv/bin/python -m src.pipeline --skip-collect`) 때 이뤄진다.
"""
import sys

import yaml

from src.classify.run import classify_all
from src.collectors import github, huggingface
from src.storage.db import connect
from src.util import ROOT, _cfg_cache, cfg, setup_logging, today_kst

PATH = ROOT / "config" / "watchlist.yaml"


def _save(wl):
    head = [l for l in PATH.read_text(encoding="utf-8").splitlines() if l.startswith("#")]
    body = yaml.safe_dump({k: wl.get(k) or [] for k in ("github", "hf_model", "hf_dataset")},
                          allow_unicode=True, sort_keys=False, default_flow_style=False)
    PATH.write_text("\n".join(head) + "\n" + body, encoding="utf-8")
    _cfg_cache.pop("watchlist", None)


def add(target):
    wl = {k: list(v or []) for k, v in cfg("watchlist").items()}
    src, name = (target.split(":", 1) if target.startswith(("hf_model:", "hf_dataset:")) else ("github", target))
    name = name.strip().removeprefix("https://github.com/").removeprefix("https://huggingface.co/").strip("/")
    if name not in wl.setdefault(src, []):
        wl[src].append(name)
        _save(wl)
    con = connect()
    today = today_kst()
    if src == "github":
        iid = github.add_repo(con, name, today)
        con.commit()
        ok = github.collect(con, today, only=[iid])
    else:
        ok = huggingface.collect_watchlist(con, today)
    classify_all(con, use_llm=False)  # 빠르게: 규칙 분류만 (미분류면 06:00 실행 때 LLM 분류)
    print(f"관심 목록에 추가: {src}:{name} (지표 수집 {ok}건)")


if __name__ == "__main__":
    setup_logging()
    if len(sys.argv) >= 3 and sys.argv[1] == "add":
        for t in sys.argv[2:]:
            add(t)
    elif len(sys.argv) >= 2 and sys.argv[1] == "list":
        print(yaml.safe_dump(cfg("watchlist"), allow_unicode=True))
    else:
        print(__doc__)
