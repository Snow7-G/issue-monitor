#!/usr/bin/env python3
"""GitHub Issue Monitor: 检查监看列表中的仓库是否有新 issue，有则推送到企业微信机器人。"""
import json
import os
import sys
import urllib.request

REPOS = [
    "langchain-ai/langchain",
    "langchain-ai/langgraph",
    "modelcontextprotocol/python-sdk",
    "crewAIInc/crewAI",
]

STATE_DIR = "monitor-state"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
WEBHOOK = os.environ["WEBHOOK_URL"]
TOKEN = os.environ["GITHUB_TOKEN"]

API_HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "issue-monitor",
}


def api_json(url):
    req = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def post_wechat(content):
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.load(r)
    if result.get("errcode") != 0:
        print(f"wechat push failed: {result}", file=sys.stderr)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return None


def save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_open_issues(repo):
    """返回该仓库当前 open issue 列表（排除 PR）。"""
    url = (f"https://api.github.com/repos/{repo}/issues"
           "?state=open&sort=created&direction=desc&per_page=50")
    data = api_json(url)
    return [i for i in data if "pull_request" not in i]


def main():
    state = load_state()

    if state is None:
        # 首轮：建立基线，不推送，避免历史 issue 刷屏
        state = {"repos": {}}
        for repo in REPOS:
            issues = fetch_open_issues(repo)
            state["repos"][repo] = [
                {"number": i["number"], "title": i["title"]} for i in issues]
            print(f"[baseline] {repo}: {len(issues)} open issues recorded")
        save_state(state)
        print("baseline established, no notification sent")
        return

    new_total = 0
    messages = []
    for repo in REPOS:
        known = {i["number"] for i in state["repos"].get(repo, [])}
        try:
            issues = fetch_open_issues(repo)
        except Exception as e:
            print(f"[error] {repo}: {e}", file=sys.stderr)
            continue
        fresh = [i for i in issues if i["number"] not in known]
        if fresh:
            new_total += len(fresh)
            lines = [f"**{repo}**（{len(fresh)} 个新）"]
            for i in fresh:
                lines.append(f"· #{i['number']} [{i['title']}]({i['html_url']})")
            messages.append("\n".join(lines))
            state["repos"][repo].extend(
                {"number": i["number"], "title": i["title"]} for i in fresh)

    if new_total:
        header = f"**🔔 GitHub 新 Issue 汇报（{new_total} 个）**\n\n"
        post_wechat(header + "\n\n".join(messages))
        print(f"pushed notification: {new_total} new issues")
    else:
        print("no new issues")

    save_state(state)


if __name__ == "__main__":
    main()
