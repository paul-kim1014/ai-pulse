"""SQLite 저장소 — 기획서 7.4 데이터 모델 (snapshots는 세로형)."""
import json
import sqlite3

from src.util import ROOT

DB_PATH = ROOT / "data" / "ai_pulse.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
  item_id TEXT PRIMARY KEY,      -- 예: github:owner/name, hf_model:org/name, hf_dataset:..., paper:2610.24087
  source TEXT NOT NULL,          -- github | hf_model | hf_dataset | paper
  type TEXT,
  name TEXT,
  title TEXT,
  url TEXT,
  description TEXT,
  created_at TEXT,               -- 저장소/모델 생성일, 논문 공개일 (YYYY-MM-DD)
  field TEXT,                    -- 대분류 키 (llm, vision, ...)
  field_by TEXT,                 -- rule | llm | override
  tags TEXT,                     -- 관심 태그 JSON 배열
  keywords TEXT,                 -- 토픽·태그·키워드 JSON 배열 (분류·급상승 키워드용)
  arxiv_id TEXT,
  family_id TEXT,
  extra TEXT,                    -- 소스별 부가 정보 JSON (카테고리, 연결 저장소 등)
  desc_ko TEXT,                  -- 한국어 한 줄 설명 (LLM 생성, 캐시)
  first_seen TEXT,
  last_seen TEXT,
  detail_at TEXT,                -- HF 상세(파생·Spaces) 마지막 조회일
  is_active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_items_source ON items(source);
CREATE INDEX IF NOT EXISTS ix_items_arxiv ON items(arxiv_id);

CREATE TABLE IF NOT EXISTS snapshots (
  item_id TEXT, date TEXT, metric TEXT, value REAL,
  PRIMARY KEY (item_id, date, metric)
);
CREATE INDEX IF NOT EXISTS ix_snap_date ON snapshots(date, metric);

CREATE TABLE IF NOT EXISTS scores (
  view TEXT, item_id TEXT, date TEXT, period TEXT, axis TEXT,
  score REAL, rank INTEGER, rank_change INTEGER, field TEXT, anomaly_flag INTEGER, period_label TEXT,
  PRIMARY KEY (view, item_id, date, period, axis)
);
CREATE INDEX IF NOT EXISTS ix_scores ON scores(view, date, period, axis, rank);

CREATE TABLE IF NOT EXISTS trends (
  date TEXT, keyword TEXT, source TEXT, recent_count INTEGER, ratio REAL,
  PRIMARY KEY (date, keyword)
);
CREATE TABLE IF NOT EXISTS summaries (date TEXT PRIMARY KEY, text TEXT, model TEXT);
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,
  source TEXT, status TEXT, items INTEGER, error TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=120)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def upsert_item(con, it, today):
    """수집기가 만든 항목 dict를 저장. 분류·설명 등 파생 컬럼은 보존한다."""
    row = con.execute("SELECT extra, keywords FROM items WHERE item_id=?", (it["item_id"],)).fetchone()
    extra = json.loads(row["extra"]) if row and row["extra"] else {}
    extra.update(it.get("extra") or {})
    kw = it.get("keywords")
    con.execute(
        """INSERT INTO items(item_id, source, type, name, title, url, description, created_at,
                             keywords, arxiv_id, extra, first_seen, last_seen)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(item_id) DO UPDATE SET
             title=COALESCE(excluded.title, title), url=COALESCE(excluded.url, url),
             description=COALESCE(excluded.description, description),
             created_at=COALESCE(excluded.created_at, created_at),
             keywords=COALESCE(excluded.keywords, keywords),
             arxiv_id=COALESCE(excluded.arxiv_id, arxiv_id),
             extra=excluded.extra, last_seen=excluded.last_seen""",
        (it["item_id"], it["source"], it.get("type"), it.get("name"), it.get("title"), it.get("url"),
         it.get("description"), it.get("created_at"),
         json.dumps(kw, ensure_ascii=False) if kw is not None else None,
         it.get("arxiv_id"), json.dumps(extra, ensure_ascii=False), today, today))


def put_metrics(con, item_id, day, metrics):
    """같은 날짜 재실행 시 덮어쓴다(멱등). None 값은 저장하지 않는다."""
    con.executemany(
        "INSERT OR REPLACE INTO snapshots(item_id, date, metric, value) VALUES(?,?,?,?)",
        [(item_id, day, k, float(v)) for k, v in metrics.items() if v is not None])


def get_meta(con, key, default=None):
    r = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?,?)", (key, value))


def log_run(con, source, started, finished, status, items, error=None):
    con.execute("INSERT INTO runs(started_at, finished_at, source, status, items, error) VALUES(?,?,?,?,?,?)",
                (started, finished, source, status, items, error))
