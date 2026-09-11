#!/usr/bin/env python3
"""GitHub Issue Monitor: 检查监看列表中的仓库是否有新 issue，打适配度分后推送到企业微信。

评分针对的用户画像（2028 届本科，找北京 agent 开发岗）：
- langchain4j / Java MCP 开源贡献经历
- 第四范式 AI 智能体实习
- 家庭智能医疗 agent + 天津眼科医院落地 agent 项目
"""
import json
import os
import re
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

# 评分核心逻辑：对简历有帮助 = 能变成开源贡献的 > 面试素材类 > 背景协同类
# (关键词, 权重, 理由)
FIT_KEYWORDS = [
    # 第一梯队：可能直接变成你的开源贡献（简历硬通货）
    (["reproducib", "minimal example", "steps to reproduce", "sample code"], 3.0, "可复现 bug（容易出贡献）"),
    (["tool call", "tool calling", "function call", "function calling", "tool use"], 2.5, "agent 核心技术（面试素材）"),
    (["multi-agent", "multi agent", "orchestrat", "supervisor", "agent team", "handoff", "graph", "state"], 2.5, "agent 核心技术（面试素材）"),
    (["rag", "retrieval", "vector", "embedding", "knowledge base", "chunk"], 2.5, "agent 核心技术（面试素材）"),
    # 第二梯队：你的背景让你切入成本低
    (["mcp", "model context protocol", "modelcontextprotocol"], 2.0, "MCP 方向（有贡献经验，切入快）"),
    (["streaming", "latency", "performance", "regression", "timeout", "crash", "memory leak", "race condition"], 1.5, "工程能力展示"),
    (["langchain4j", "spring ai", "jvm", "jdk", "maven", "gradle"], 1.5, "Java 生态协同"),
    (["java"], 1.5, "Java 生态协同"),
    # 第三梯队：谈资类
    (["medical", "health", "clinical", "hospital", "diagnos", "patient"], 1.0, "医疗领域素材"),
    (["llm", "prompt", "chat model", "chatmodel"], 0.5, "LLM 基础"),
]

# issue 上的 label 加分（贡献机会的最强信号）
FIT_LABELS = {
    "good first issue": (3.5, "官方标记新手可做"),
    "help wanted": (3.0, "官方求贡献"),
    "bug": (1.5, "bug 修复机会"),
    "enhancement": (1.5, "feature 实现机会"),
    "feature request": (1.5, "feature 实现机会"),
}

# 减分项：对求职帮助小的类型
UNFIT_KEYWORDS = [
    (["docs", "documentation", "typo", "readme", "translate", "translation", " spelling"], -2.0, "文档类"),
    (["website", "css", "logo", "style", "favicon", "landing page"], -2.0, "前端外观类"),
    (["question", "how to", "usage question"], -1.0, "使用咨询类"),
    (["deprecated removal", "breaking change"], -0.5, "破坏性变更公告"),
]

BASE_SCORE = 1.5  # 基础分


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


def score_issue(issue):
    """按「对简历的帮助程度」打分：0-10，保留 0.5 粒度，返回 (分数, 理由)。"""
    title = issue.get("title", "") or ""
    body = (issue.get("body", "") or "")[:600]
    text = (title + " " + body).lower()

    score = BASE_SCORE
    reasons = []
    for kws, weight, why in FIT_KEYWORDS:
        for kw in kws:
            if kw == "java":
                matched = re.search(r"(?<![a-z0-9])java(?!script)", text)
            else:
                matched = re.search(r"(?<![a-z0-9])" + re.escape(kw), text)
            if matched:
                score += weight
                if why not in reasons:
                    reasons.append(why)
                break

    # label 是贡献机会最强的信号，权重高于正文关键词
    labels = {l.get("name", "").lower() for l in issue.get("labels", []) if isinstance(l, dict)}
    for label, (weight, why) in FIT_LABELS.items():
        if label in labels:
            score += weight
            if why not in reasons:
                reasons.insert(0, why)

    for kws, weight, why in UNFIT_KEYWORDS:
        for kw in kws:
            if kw in text:
                score += weight
                if why not in reasons:
                    reasons.append(why)
                break

    score = max(0.0, min(10.0, score))
    score = round(score * 2) / 2
    reason = " + ".join(reasons[:2]) if reasons else "通用 issue"
    return score, reason


def load_fit_config():
    """监看列表与评分规则同仓库维护，暂时硬编码在上方常量。"""
    return None


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
    repo_sections = []
    for repo in REPOS:
        known = {i["number"] for i in state["repos"].get(repo, [])}
        try:
            issues = fetch_open_issues(repo)
        except Exception as e:
            print(f"[error] {repo}: {e}", file=sys.stderr)
            continue
        fresh = [i for i in issues if i["number"] not in known]
        if fresh:
            scored = []
            for i in fresh:
                s, reason = score_issue(i)
                scored.append((s, reason, i))
            scored.sort(key=lambda x: -x[0])

            body = [f"**📁 {repo}**（{len(fresh)} 个新）", ""]
            for idx, (s, reason, i) in enumerate(scored):
                mark = " 🔥" if s >= 7 else (" ⭐" if s >= 5 else "")
                body.append(f"**{s}分**{mark} · #{i['number']}")
                body.append(f"[{i['title']}]({i['html_url']})")
                if s >= 5:
                    body.append(f"→ {reason}")
                if idx < len(scored) - 1:
                    body.append("————————")
                else:
                    body.append("")
            repo_sections.append("\n".join(body))

            state["repos"][repo].extend(
                {"number": i["number"], "title": i["title"]} for i in fresh)
            new_total += len(fresh)

    if new_total:
        header = f"**🔔 GitHub 新 Issue 汇报（{new_total} 个，按简历贡献价值排序）**\n\n"
        post_wechat(header + "\n\n".join(repo_sections))
        print(f"pushed notification: {new_total} new issues")
    else:
        print("no new issues")

    save_state(state)


if __name__ == "__main__":
    main()
