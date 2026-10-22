"""GitHub 수집기 — 검색 API로 후보 발견, GraphQL로 지표 묶음 조회.

인증은 `gh` CLI 로그인을 그대로 사용한다 (토큰을 파일에 두지 않음).
주의: 2026년 현재 GitHub는 stargazer 목록(시점 포함) 조회를 막아 두어 스타 이력 백필이 불가하다.
따라서 스타·포크 증가량은 매일 쌓이는 스냅샷 차이로만 계산한다.
"""
import json
import subprocess
import time
from datetime import timedelta

from src.storage.db import put_metrics, upsert_item
from src.util import cfg, log, parse_date

GH = "/opt/homebrew/bin/gh"


def _gh(args, retries=3):
    last = None
    for i in range(retries):
        p = subprocess.run([GH, "api", *args], capture_output=True, text=True, timeout=180)
        out = p.stdout.strip()
        if out:
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = None
            if data is not None and (p.returncode == 0 or "data" in data):
                return data
        last = (p.stderr or out)[:300]
        if "rate limit" in last.lower() or "secondary" in last.lower():
            time.sleep(60)
        else:
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"gh api 실패: {last}")


def _search(q, pages, per_page=100):
    found = []
    for page in range(1, pages + 1):
        data = _gh(["-X", "GET", "search/repositories", "-f", f"q={q}", "-f", "sort=stars",
                    "-f", "order=desc", "-f", f"per_page={per_page}", "-f", f"page={page}"])
        items = data.get("items", [])
        found.extend(items)
        time.sleep(2.2)  # 검색 API 분당 30회
        if len(items) < per_page:
            break
    return found


def discover(con, today, extra_repos=()):
    """편입 조건을 만족하는 저장소를 찾아 items에 추가. 반환: 신규 편입 수."""
    c = cfg("sources")["github"]
    cands = {}
    queries = [(f"topic:{t} stars:>={c['min_stars']}", c["pages_per_topic"]) for t in c["topics"]]
    since35 = (today - timedelta(days=35)).isoformat()
    queries += [(f"{q} in:name,description,topics created:>={since35} stars:>={c['new_repo_min_stars']}", 2)
                for q in c["new_repo_queries"]]
    queries += [(f"topic:{t} created:>={today.year}-01-01 stars:>=300", 1) for t in c["topics"]]
    rh = c.get("recent_hot")
    if rh:
        since = (today - timedelta(days=rh["days"])).isoformat()
        queries += [(f"{q} in:name,description,topics created:>={since} stars:>={rh['min_stars']}", rh["pages"])
                    for q in c["new_repo_queries"]]
    # 지난달 신규 (월간 코호트용)
    first = today.replace(day=1)
    prev_first = (first - timedelta(days=1)).replace(day=1)
    queries += [(f"{q} in:name,description,topics created:{prev_first}..{first - timedelta(days=1)} stars:>=50", 1)
                for q in c["new_repo_queries"]]
    for q, pages in queries:
        try:
            for r in _search(q, pages):
                cands[r["full_name"].lower()] = r
        except Exception as e:  # 한 쿼리 실패가 전체를 막지 않도록
            log.warning("GitHub 검색 실패 %s: %s", q, e)
    before = con.execute("SELECT COUNT(*) FROM items WHERE source='github'").fetchone()[0]
    for full, r in cands.items():
        if r.get("fork"):
            continue
        upsert_item(con, {
            "item_id": f"github:{full}", "source": "github", "type": "repo", "name": r["full_name"],
            "title": r["full_name"], "url": r["html_url"], "description": (r.get("description") or "")[:500],
            "created_at": (r.get("created_at") or "")[:10], "keywords": r.get("topics") or [],
        }, today.isoformat())
    for full in cfg("watchlist").get("github") or []:  # 관심 목록은 조건과 무관하게 추적
        add_repo(con, full, today)
    for full, arxiv_id in extra_repos:  # 추적 논문의 공식 코드 저장소
        con.execute("INSERT OR IGNORE INTO items(item_id, source, type, name, title, url, first_seen, last_seen, extra)"
                    " VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"github:{full.lower()}", "github", "repo", full, full, f"https://github.com/{full}",
                     today.isoformat(), today.isoformat(), json.dumps({"paper": arxiv_id})))
        con.execute("UPDATE items SET arxiv_id=COALESCE(arxiv_id, ?) WHERE item_id=?",
                    (arxiv_id, f"github:{full.lower()}"))
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM items WHERE source='github'").fetchone()[0]
    return after - before, len(cands)


def add_repo(con, full, today, extra=None):
    """저장소 1개를 추적 대상에 넣는다 (이미 있으면 다시 활성화)."""
    iid = f"github:{full.lower()}"
    con.execute("INSERT OR IGNORE INTO items(item_id, source, type, name, title, url, first_seen, last_seen, extra)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (iid, "github", "repo", full, full, f"https://github.com/{full}", today.isoformat(),
                 today.isoformat(), json.dumps(extra or {})))
    con.execute("UPDATE items SET is_active=1 WHERE item_id=?", (iid,))
    return iid


FIELDS = """nameWithOwner url description createdAt pushedAt isFork isMirror isArchived
stargazerCount forkCount watchers{totalCount} issues{totalCount} pullRequests{totalCount}
mentionableUsers{totalCount} repositoryTopics(first:20){nodes{topic{name}}}
defaultBranchRef{target{... on Commit{history(since:"%s"){totalCount}}}}"""


def _query_split(parts, offset):
    """묶음 조회. 502(서버 시간 초과)가 나면 반으로 나눠 다시 시도한다."""
    q = "query{rateLimit{remaining resetAt} " + " ".join(parts) + "}"
    try:
        return _gh(["graphql", "-f", f"query={q}"], retries=2).get("data") or {}
    except Exception as e:
        if len(parts) <= 3:
            log.warning("GraphQL 묶음 실패(%d~): %s", offset, e)
            return {}
        half = len(parts) // 2
        a, b = _query_split(parts[:half], offset), _query_split(parts[half:], offset + half)
        return {**a, **b}


def collect(con, today, only=None):
    """추적 중인 모든 저장소(또는 only 목록)의 당일 지표를 GraphQL로 25개씩 묶어 조회."""
    batch = cfg("sources")["github"]["graphql_batch"]
    ids = only or [r["item_id"] for r in con.execute(
        "SELECT item_id FROM items WHERE source='github' AND is_active=1 ORDER BY item_id")]
    since = (today - timedelta(days=365)).isoformat() + "T00:00:00Z"
    fields = FIELDS % since
    ok = 0
    for i in range(0, len(ids), batch):
        chunk = ids[i:i + batch]
        parts = []
        for j, iid in enumerate(chunk):
            owner, name = iid.split(":", 1)[1].split("/", 1)
            parts.append(f'r{j}:repository(owner:{json.dumps(owner)},name:{json.dumps(name)}){{{fields}}}')
        data = _query_split(parts, i)
        rl = data.get("rateLimit") or {}
        if rl.get("remaining", 9999) < 200:
            log.info("GraphQL 잔여 한도 %s → 리셋까지 대기", rl.get("remaining"))
            time.sleep(600)
        for j, iid in enumerate(chunk):
            r = data.get(f"r{j}")
            if not r:
                con.execute("UPDATE items SET is_active=0 WHERE item_id=? AND last_seen<?",
                            (iid, (today - timedelta(days=14)).isoformat()))
                continue
            if r["isFork"] or r["isMirror"]:  # 미러·포크는 순위 제외 (기획서 3.4)
                con.execute("UPDATE items SET is_active=0 WHERE item_id=?", (iid,))
                continue
            topics = [n["topic"]["name"] for n in r["repositoryTopics"]["nodes"]]
            hist = ((r.get("defaultBranchRef") or {}).get("target") or {}).get("history") or {}
            pushed = parse_date(r["pushedAt"])
            upsert_item(con, {
                "item_id": iid, "source": "github", "name": r["nameWithOwner"], "title": r["nameWithOwner"],
                "url": r["url"], "description": (r.get("description") or "")[:500],
                "created_at": r["createdAt"][:10], "keywords": topics,
                "extra": {"archived": r["isArchived"]},
            }, today.isoformat())
            put_metrics(con, iid, today.isoformat(), {
                "stars": r["stargazerCount"], "forks": r["forkCount"],
                "watchers": r["watchers"]["totalCount"],
                "issues_prs": r["issues"]["totalCount"] + r["pullRequests"]["totalCount"],
                "contributors": r["mentionableUsers"]["totalCount"],
                "commits_1y": hist.get("totalCount"),
                "days_since_push": (today - pushed).days if pushed else None,
            })
            ok += 1
        con.commit()
    return ok
