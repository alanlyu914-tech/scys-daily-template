#!/usr/bin/env python3
# 生财有术 MCP 授权脚本（一次性）：OAuth 2.1 + PKCE + DCR + loopback 回调
# 运行后会自动打开浏览器 → 登录生财有术并同意授权 → 生成 ~/.scys-mcp-auth.json
# 仅标准库，Mac/Linux/Git Bash 通用
import os, sys, json, secrets, hashlib, base64, urllib.parse, http.server, socketserver, threading, subprocess, time, urllib.request

AS = "https://mcp.scys.com/mcp-oauth"
RS = "https://mcp.scys.com/shengcai-web/mcp"
PORT = 8911
REDIRECT = "http://127.0.0.1:%d/callback" % PORT
AUTHFILE = os.path.expanduser("~/.scys-mcp-auth.json")

def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

def post_form(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")

print("[1/5] 注册客户端（DCR，无需任何密钥）...")
st, body = post_json(AS + "/register", {
    "client_name": "scys-daily-" + secrets.token_hex(4),
    "redirect_uris": [REDIRECT],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
    "scope": "mcp",
})
if st not in (200, 201):
    print("注册失败：", st, body[:200]); sys.exit(2)
client_id = json.loads(body)["client_id"]
print("     成功（public client）")

verifier = secrets.token_urlsafe(64)[:96]
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state = secrets.token_urlsafe(24)
print("[2/5] 生成 PKCE...OK")

params = {"response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
          "scope": "mcp", "code_challenge": challenge, "code_challenge_method": "S256",
          "state": state, "resource": RS}
url = AS + "/authorize?" + urllib.parse.urlencode(params)
print("[3/5] 打开浏览器，请在生财有术页面登录并【同意授权】...")
subprocess.run(["open", url] if sys.platform == "darwin" else ["xdg-open", url], check=False)
print("     若浏览器没弹出，手动打开下面这个链接：")
print("     " + url)

holder = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(p.query)
        if p.path == "/callback":
            holder.update({k: q.get(k, [None])[0] for k in ("code", "state", "error")})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
            self.wfile.write("✅ 授权成功！可以关闭此页，回到终端继续。".encode("utf-8"))
        else:
            self.send_response(404); self.end_headers()
    def log_message(self, *a):
        pass
httpd = socketserver.TCPServer(("127.0.0.1", PORT), H)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
print("[4/5] 等待授权回调（最长 5 分钟，请在浏览器完成同意）...")
t0 = time.time()
while time.time() - t0 < 300 and "code" not in holder and "error" not in holder:
    time.sleep(0.4)
httpd.shutdown()
if holder.get("error") or not holder.get("code"):
    print("未收到授权（超时或被拒绝）：" + str(holder.get("error") or "timeout")); sys.exit(4)
if holder.get("state") != state:
    print("state 校验失败"); sys.exit(5)
print("     回调已接收")

st, body = post_form(AS + "/token", {
    "grant_type": "authorization_code", "code": holder["code"], "redirect_uri": REDIRECT,
    "client_id": client_id, "code_verifier": verifier, "resource": RS})
tok = {}
try:
    tok = json.loads(body)
except Exception:
    pass
if st != 200 or not tok.get("access_token"):
    print("换取令牌失败：", st, body[:200]); sys.exit(6)
auth = {"client_id": client_id, "access_token": tok["access_token"],
        "refresh_token": tok.get("refresh_token"), "expires_in": tok.get("expires_in", 3600),
        "obtained_at": int(time.time())}
with open(AUTHFILE, "w") as f:
    json.dump(auth, f)
os.chmod(AUTHFILE, 0o600)
print("[5/5] 完成！授权文件已保存到 ~/.scys-mcp-auth.json（含长效 refresh_token）")
print("下一步：按教程把它加密成 state/auth.json.enc 并提交到你的仓库。")
