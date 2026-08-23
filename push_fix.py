#!/usr/bin/env python3
"""补推 AP 站漏掉的 2 个清理后文件。Token 从 git remote URL 读取，不硬编码。"""
import base64, os, re, subprocess, sys, json
import requests

s = requests.Session()
s.trust_env = False

url = subprocess.check_output(
    ["git", "config", "--get", "remote.origin.url"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
).decode().strip()
m = re.match(r"https://([^:]+):([^@]+)@github\.com/(.+?)(?:\.git)?$", url)
if not m:
    sys.exit("无法从 remote URL 解析凭证")
TOKEN, REPO = m.group(2), m.group(3)
API = f"https://api.github.com/repos/{REPO}/contents"
H = {"Authorization": f"token {TOKEN}", "Accept": "application/vnd.github.v3+json"}
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")

FILES = [
    "index.html",
]

for rel in FILES:
    lp = os.path.join(BASE, rel)
    rp = f"docs/{rel}"
    u = f"{API}/{rp}"
    content = base64.b64encode(open(lp, "rb").read()).decode()
    r = s.get(u, headers=H, timeout=60)
    sha = r.json().get("sha") if r.status_code == 200 else None
    payload = {"message": f"fix: MCQ filter counts non-empty options (hides 102 blank-option questions) - {rel}",
               "content": content}
    if sha:
        payload["sha"] = sha
    r = s.put(u, headers=H, json=payload, timeout=120)
    print(("  OK  " if r.status_code in (200, 201) else f"  FAIL {r.status_code} {r.text[:200]}"), rp)
print("done")
