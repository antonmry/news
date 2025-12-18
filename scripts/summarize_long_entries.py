#!/usr/bin/env python3
import argparse
import json
import os
import sys
from datetime import date, timedelta
from typing import Optional, Tuple
from urllib.request import Request, urlopen


def _split_link(line: str) -> Tuple[str, str]:
    marker = " [Link]("
    idx = line.rfind(marker)
    if idx == -1:
        return line, ""
    return line[:idx], line[idx:]


def _call_github_models(prompt: str, max_chars: int) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_MODELS_TOKEN.")
    model = os.environ.get("GITHUB_MODELS_MODEL", "gpt-5-nano")
    api_url = f"https://api.github.com/ai/models/{model}/inference"

    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "Summarize the text to fit within the requested character limit. "
                    "Preserve key details, names, and links mentioned in the text. "
                    "Return plain text only."
                ),
            },
            {
                "role": "user",
                "content": f"Limit: {max_chars} characters.\nText: {prompt}",
            },
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }

    body = json.dumps(payload).encode("utf-8")
    req = Request(
        api_url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return content.strip()


def _summarize_line(line: str, max_chars: int) -> str:
    if not line.startswith("- "):
        return line
    message, link_part = _split_link(line)
    text = message[2:].strip()
    if len(text) <= max_chars:
        return line
    summary = _call_github_models(text, max_chars)
    if not summary:
        return line
    return f"- {summary}{link_part}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize long markdown entries via GitHub Models.")
    parser.add_argument("--file", help="Markdown file to process.")
    parser.add_argument("--max-chars", type=int, default=300, help="Max characters per entry.")
    args = parser.parse_args()

    report_date = (date.today() - timedelta(days=1)).isoformat()
    path = args.file or f"{report_date}.md"

    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    updated = []
    for line in lines:
        updated.append(_summarize_line(line, args.max_chars))

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
