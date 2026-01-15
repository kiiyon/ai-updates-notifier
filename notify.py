import json
import os
import time
from typing import Dict, Any, List, Optional

import feedparser
import requests

STATE_FILE = "state.json"

OPENAI_RSS = "https://openai.com/news/rss.xml"
# 監視対象パッケージリスト
NPM_PACKAGES = [
    "@openai/codex",
    "@anthropic-ai/claude-code",
]
# 監視対象リポジトリリスト
GITHUB_RELEASES = [
    # (owner, repo, label)
    ("openai", "codex", "openai/codex"),
]

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {"npm": {}, "github": {}, "rss": {}}
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def discord_post(webhook: str, content: str) -> None:
    # Discordの2000文字制限をざっくりケア
    chunks = [content[i:i+1900] for i in range(0, len(content), 1900)]
    for c in chunks:
        r = requests.post(webhook, json={"content": c}, timeout=20)
        r.raise_for_status()
        time.sleep(0.3)

def get_npm_latest(pkg: str) -> Optional[str]:
    # npm registry API
    # scoped package は URL encode が必要
    from urllib.parse import quote
    url = f"https://registry.npmjs.org/{quote(pkg, safe='')}"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        return data.get("dist-tags", {}).get("latest")
    except Exception:
        return None

def get_github_latest_release(owner: str, repo: str) -> Optional[Dict[str, str]]:
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return None
        data = r.json()
        return {
            "tag": data.get("tag_name", ""),
            "name": data.get("name", "") or data.get("tag_name", ""),
            "url": data.get("html_url", ""),
        }
    except Exception:
        return None

def get_openai_rss_latest_id() -> Optional[Dict[str, str]]:
    try:
        feed = feedparser.parse(OPENAI_RSS)
        if not feed.entries:
            return None
        e = feed.entries[0]
        return {
            "title": getattr(e, "title", ""),
            "link": getattr(e, "link", ""),
            "id": getattr(e, "id", getattr(e, "link", "")),
        }
    except Exception:
        return None

def main():
    webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook:
        # ローカル実行でまだ設定されていない場合などのために、エラーではなくプリントで終了する手もあるが
        # GitHub Actionsで気づけるようにエラーにする
        print("Warning: DISCORD_WEBHOOK_URL is not set. Skipping notifications.")
        return

    state = load_state()
    notifications = {"OpenAI": [], "Anthropic": [], "Other": []}

    # Helper function to categorize
    def add_notification(text: str, source_name: str):
        lower_text = (text + source_name).lower()
        if "openai" in lower_text:
            notifications["OpenAI"].append(text)
        elif "anthropic" in lower_text or "claude" in lower_text:
            notifications["Anthropic"].append(text)
        else:
            notifications["Other"].append(text)

    # 1) npm
    print("Checking npm packages...")
    for pkg in NPM_PACKAGES:
        latest = get_npm_latest(pkg)
        if not latest:
            continue
        prev = state.get("npm", {}).get(pkg)
        if prev != latest:
            if "npm" not in state: state["npm"] = {}
            state["npm"][pkg] = latest
            msg = f"📦 npm: `{pkg}` → **{latest}**\nhttps://www.npmjs.com/package/{pkg}"
            add_notification(msg, pkg)

    # 2) GitHub releases
    print("Checking GitHub releases...")
    for owner, repo, label in GITHUB_RELEASES:
        rel = get_github_latest_release(owner, repo)
        if not rel or not rel["tag"]:
            continue
        prev = state.get("github", {}).get(label)
        if prev != rel["tag"]:
            if "github" not in state: state["github"] = {}
            state["github"][label] = rel["tag"]
            msg = f"🏷️ GitHub: `{label}` → **{rel['name']}** ({rel['tag']})\n{rel['url']}"
            add_notification(msg, label)

    # 3) OpenAI RSS
    print("Checking OpenAI RSS...")
    latest = get_openai_rss_latest_id()
    if latest and latest["id"]:
        prev = state.get("rss", {}).get("openai_news")
        if prev != latest["id"]:
            if "rss" not in state: state["rss"] = {}
            state["rss"]["openai_news"] = latest["id"]
            msg = f"📰 News: **{latest['title']}**\n{latest['link']}"
            add_notification(msg, "openai")

    # Construct Message
    final_blocks = []
    
    # OpenAI Section
    if notifications["OpenAI"]:
        final_blocks.append("🟦 **OpenAI Updates**")
        final_blocks.extend(notifications["OpenAI"])
        final_blocks.append("") # Spacer

    # Anthropic Section
    if notifications["Anthropic"]:
        final_blocks.append("🟧 **Anthropic Updates**")
        final_blocks.extend(notifications["Anthropic"])
        final_blocks.append("") # Spacer

    # Other Section
    if notifications["Other"]:
        final_blocks.append("⬜ **Other Updates**")
        final_blocks.extend(notifications["Other"])
    
    if final_blocks:
        # Remove trailing empty string if exists
        if final_blocks[-1] == "": final_blocks.pop()
        
        print("Sending notifications to Discord...")
        # Join with newlines
        full_msg = "\n".join(final_blocks)
        discord_post(webhook, f"🚨 **AI Tech Updates**\n\n{full_msg}")
    else:
        print("No new updates.")

    save_state(state)

if __name__ == "__main__":
    main()
