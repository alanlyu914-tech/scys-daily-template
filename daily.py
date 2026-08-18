#!/usr/bin/env python3
# 生财有术每日看板 · 云端版（GitHub Actions / 本地通用，仅标准库）
# 流程：解密token → refresh(自动轮换) → MCP检索×5 → 规则选帖 → 14天去重 → 组板 → 飞书Webhook推送 → 回写加密状态
import os, sys, json, time, re, subprocess, urllib.request, urllib.parse, datetime

AS = "https://mcp.scys.com/mcp-oauth"
RS = "https://mcp.scys.com/shengcai-web/mcp"
STATE_AUTH_ENC = "state/auth.json.enc"
STATE_HISTORY = "state/history.json"
KEY = os.environ.get("STATE_KEY", "")
WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")
LOCAL_SYNC_FILE = os.path.expanduser("~/.scys-mcp-auth.json")
TODAY = datetime.date.today().isoformat()
DEDUP_DAYS = 14

def die(msg, code=1):
    print("FATAL:", msg)
    sys.exit(code)

# ---------- 状态读写（AES-256-CBC，密钥仅经环境变量） ----------
def sh(cmd, env_extra=None, stdin_data=None):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, input=stdin_data)

def load_auth():
    if not KEY:
        die("STATE_KEY not set")
    r = sh(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:STATE_KEY", "-in", STATE_AUTH_ENC],
           env_extra={"STATE_KEY": KEY})
    if r.returncode != 0:
        die("decrypt auth failed: " + r.stderr.strip()[:150], 2)
    return json.loads(r.stdout)

def save_auth(a):
    r = sh(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-pass", "env:STATE_KEY", "-out", STATE_AUTH_ENC],
           env_extra={"STATE_KEY": KEY}, stdin_data=json.dumps(a))
    if r.returncode != 0:
        die("encrypt auth failed: " + r.stderr.strip()[:150], 3)
    os.chmod(STATE_AUTH_ENC, 0o600)

def load_history():
    if os.path.exists(STATE_HISTORY):
        try:
            return json.load(open(STATE_HISTORY))
        except Exception:
            pass
    return {}

def save_history(h):
    cutoff = (datetime.date.today() - datetime.timedelta(days=DEDUP_DAYS)).isoformat()
    h = {k: v for k, v in h.items() if v >= cutoff}
    json.dump(h, open(STATE_HISTORY, "w"), ensure_ascii=False, indent=1)

# ---------- OAuth：刷新并轮换 ----------
def http_post(url, data=None, headers=None, js=None):
    hdr = dict(headers or {})
    body = None
    if js is not None:
        body = json.dumps(js).encode(); hdr["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode(); hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr)
    try:
        r = urllib.request.urlopen(req, timeout=40)
        return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

def refresh(auth):
    st, body = http_post(AS + "/token", data={
        "grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
        "client_id": auth["client_id"], "resource": RS})
    tok = {}
    try:
        tok = json.loads(body)
    except Exception:
        pass
    if st != 200 or not tok.get("access_token"):
        die("token refresh failed (%s): %s — 需在本地重新发起 OAuth 授权" % (st, body[:150]), 4)
    auth["access_token"] = tok["access_token"]
    if tok.get("refresh_token"):
        auth["refresh_token"] = tok["refresh_token"]  # 轮换
    auth["expires_in"] = tok.get("expires_in", 3600)
    auth["obtained_at"] = int(time.time())
    return auth

# ---------- MCP ----------
def mcp_call(token, method, params=None, iid=None):
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
           "Authorization": "Bearer " + token}
    b = {"jsonrpc": "2.0", "method": method}
    if iid is not None:
        b["id"] = iid
    if params is not None:
        b["params"] = params
    req = urllib.request.Request(RS, data=json.dumps(b).encode(), headers=hdr)
    resp = urllib.request.urlopen(req, timeout=60)
    txt = resp.read().decode(errors="replace")
    try:
        return json.loads(txt)
    except Exception:
        for line in txt.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return {}

def fetch_candidates(token):
    searches = [("good", "AI"), ("fxb", "AI变现"), ("fxb", "AI"), ("fxb", "副业"), ("fxb", "生意")]
    pool = {}
    iid = 10
    for scene, kw in searches:
        r = mcp_call(token, "tools/call", {"name": "contentSearch",
                   "arguments": {"keyword": kw, "pageScene": scene, "pageSize": 15}}, iid)
        iid += 1
        items = []
        try:
            res = r.get("result") or {}
            txt = "".join(i.get("text", "") for i in (res.get("content") or []) if i.get("type") == "text")
            items = (json.loads(txt).get("topicDetailDTO") or {}).get("items") or []
        except Exception:
            pass
        for it in items:
            t = it.get("topicDTO") or {}
            tid = str(t.get("topicId") or t.get("entityId") or "")
            if tid and tid not in pool:
                pool[tid] = t
    return pool

# ---------- 选帖规则 ----------
def clean_text(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()

def is_ai(title):
    # (?<![a-z])ai(?![a-z]) 大小写不敏感地匹配独立 ai（避开 Airbnb/email 等），另覆盖常见写法
    return bool(re.search(r"(?i)(?<![a-z])ai(?![a-z])|aigc|agi|gpt|claude|gemini|大模型|人工智能|智能体|agent|数字人", title or ""))

def is_monetize(title):
    return bool(re.search(r"变现|赚|收入|佣金|流水|月入|付费|会员|售价|卖出|营收|定价|利润|商业模式|接单|商单", title or ""))

def is_efficiency(title):
    return bool(re.search(r"提效|效率|自动化|工作流|降本|生产力|搭建", title or ""))

def parse_post_time(v):
    """gmtCreate 兼容秒/毫秒 → datetime；缺失或超范围（<2015 或 >明年）返回 None。
    生财接口实测为秒级时间戳，历史版本误按毫秒处理导致显示 1970-01-2x。"""
    try:
        ts = int(v)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    if ts < 10**12:  # 秒级（~1.7e9）→ 毫秒
        ts *= 1000
    try:
        dt = datetime.datetime.fromtimestamp(ts / 1000)
    except (OverflowError, OSError, ValueError):
        return None
    if dt.year < 2015 or dt.year > datetime.date.today().year + 1:
        return None
    return dt

def recency_bonus(v):
    dt = parse_post_time(v)
    if not dt:
        return 0
    d = (datetime.date.today() - dt.date()).days
    return 120 if d <= 30 else (50 if d <= 60 else (15 if d <= 90 else 0))

def score(t):
    return ((t.get("likeCount") or 0) * 2 + (t.get("commentsCount") or 0) * 3
            + (t.get("readingCount") or 0) // 100 + recency_bonus(t.get("gmtCreate")))

def monetize_bonus(title):
    return (80 if is_monetize(title) else 0) + (40 if is_efficiency(title) else 0)

def intro_of(t):
    s = clean_text(t.get("aiSummaryContent") or "")
    s = re.sub(r"信息来源：[^。]*。?", "", s)
    s = re.sub(r"^(一句话总结|业务逻辑|细分需求|变现方式)：\s*", "", s)
    s = s.strip(" ：;；,，")
    if s:
        return s[:40] + ("…" if len(s) > 40 else "")
    return (t.get("showTitle") or "")[:40]

def fmt_post_date(v):
    """生财站内发帖时间 → YYYY-MM-DD，解析失败显示「时间未知」"""
    dt = parse_post_time(v)
    return dt.strftime("%Y-%m-%d") if dt else "时间未知"

def pick(pool, history):
    cands = []
    for tid, t in pool.items():
        if tid in history:
            continue
        title = t.get("showTitle") or ""
        if not title or (t.get("gmtCreate") and recency_bonus(t.get("gmtCreate")) == 0 and False):
            continue
        cands.append((tid, t, title))
    def take(pred, extra=lambda title: 0, n=1):
        got = []
        for tid, t, title in sorted(cands, key=lambda x: -score(x[1]) - extra(x[2])):
            if len(got) >= n:
                break
            if tid in [g[0] for g in got]:
                continue
            if pred(t, title):
                got.append((tid, t, title))
        return got
    sel = []
    used = set()
    def take_n(n, *preds):
        """逐级放宽：先用 preds[0]，不满额再依次放宽到后续谓词"""
        got = []
        for pred in preds:
            need = n - len(got)
            if need <= 0:
                break
            for tid, t, title in sorted(cands, key=lambda x: -score(x[1]) - monetize_bonus(x[2])):
                if len(got) >= n:
                    break
                if tid in used:
                    continue
                if pred(t, title):
                    got.append((tid, t, title))
        for g in got:
            used.add(g[0])
        return got
    # 1 精华AI：精华+AI → 放宽：任意精华
    sel += take_n(1, lambda t, ti: t.get("isDigested") and is_ai(ti),
                     lambda t, ti: t.get("isDigested"))
    # 2 风向标AI变现：AI+变现 → 放宽：AI
    sel += take_n(2, lambda t, ti: is_ai(ti) and is_monetize(ti),
                     lambda t, ti: is_ai(ti))
    # 2 风向标非AI
    sel += take_n(2, lambda t, ti: not is_ai(ti))
    return sel[:5]

# ---------- 组板 ----------
def compose(sel):
    lines = []
    def item(no, t, tag=""):
        title = t.get("showTitle") or ""
        line = "**%d. %s**\n%s（%s赞%s评%s读 · 发帖 %s）\n[原文链接](https://scys.com/topic/detail?id=%s)" % (
            no, title, intro_of(t), t.get("likeCount") or 0, t.get("commentsCount") or 0,
            t.get("readingCount") or 0, fmt_post_date(t.get("gmtCreate")),
            t.get("topicId") or t.get("entityId"))
        return line
    parts = []
    if sel:
        parts.append("### 🔥 精华帖推荐（AI 相关）\n" + item(1, sel[0][1]))
    rest = sel[1:]
    ai = [x for x in rest if is_ai(x[2])][:2]
    other = [x for x in rest if not is_ai(x[2])][:2]
    body = ""
    no = 2
    if ai:
        body += "\n\n### 🌬 风向标 ·【AI 变现】\n" + "\n\n".join(item(no + i, x[1]) for i, x in enumerate(ai))
        no += len(ai)
    if other:
        body += "\n\n### 🌬 风向标 ·【其他风向】\n" + "\n\n".join(item(no + i, x[1]) for i, x in enumerate(other))
    return "".join(parts) + body

def send_feishu(md):
    if not WEBHOOK:
        print("[webhook] FEISHU_WEBHOOK 未设置，跳过发送（仅本地预览）")
        return False
    card = {"msg_type": "interactive", "card": {
        "header": {"title": {"tag": "plain_text", "text": "📌 今日生财重点内容 · " + TODAY}},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": md}}]}}
    st, body = http_post(WEBHOOK, js=card)
    ok = st == 200 and json.loads(body).get("code") == 0
    print("[webhook] HTTP", st, body[:120])
    return ok

# ---------- 主流程 ----------
def main():
    auth = load_auth()
    auth = refresh(auth)
    token = auth["access_token"]
    init = mcp_call(token, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                          "clientInfo": {"name": "scys-daily-cloud", "version": "1.0"}}, 1)
    mcp_call(token, "notifications/initialized")
    pool = fetch_candidates(token)
    print("[pool] %d candidates" % len(pool))
    if len(pool) < 5:
        die("候选不足 5 条", 5)
    history = load_history()
    sel = pick(pool, history)
    if len(sel) < 5:
        die("筛选后不足 5 条（去重后无合适候选）", 6)
    md = compose(sel)
    print("=" * 40)
    print("📌 今日生财重点内容 · " + TODAY)
    print(md)
    print("=" * 40)
    sent = send_feishu(md)
    with open("state/board.md", "w") as f:
        f.write("📌 今日生财重点内容 · " + TODAY + "\n\n" + md + "\n")
    for tid, t, _ in sel:
        history[tid] = TODAY
    save_history(history)
    save_auth(auth)
    # 本地联跑时同步明文状态（供 ZCode 兜底脚本继续可用）
    if os.environ.get("SCYS_LOCAL_SYNC") == "1" and os.path.exists(os.path.dirname(LOCAL_SYNC_FILE)):
        json.dump(auth, open(LOCAL_SYNC_FILE, "w"))
        os.chmod(LOCAL_SYNC_FILE, 0o600)
    print("[done] selected=%d sent=%s history=%d" % (len(sel), sent, len(history)))

if __name__ == "__main__":
    main()
