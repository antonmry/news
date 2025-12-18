#!/usr/bin/env python3
import argparse
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple

try:
    from azure.ai.inference import ChatCompletionsClient
    from azure.ai.inference.models import SystemMessage, UserMessage
    from azure.core.credentials import AzureKeyCredential
except Exception as exc:  # pragma: no cover
    raise RuntimeError(
        "azure-ai-inference is required. Run with "
        "`uv tool run --from azure-ai-inference python3 scripts/summarize_long_entries.py`."
    ) from exc


def _split_link(line: str) -> Tuple[str, str]:
    idx = line.rfind("](")
    if idx == -1:
        return line, ""
    start = line.rfind(" [", 0, idx)
    if start == -1:
        return line, ""
    return line[:start], line[start:]


def _call_github_models(prompt: str, max_chars: int) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_MODELS_TOKEN.")
    model = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-5-nano")
    endpoint = os.environ.get("GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference")

    client = ChatCompletionsClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(token),
    )

    response = client.complete(
        messages=[
            SystemMessage(
                "Summarize the text to fit within the requested character limit. "
                "Preserve key details, names, and links mentioned in the text. "
                "Return plain text only."
            ),
            UserMessage(f"Limit: {max_chars} characters.\nText: {prompt}"),
        ],
        model=model,
    )

    content = (response.choices[0].message.content or "").strip()
    if len(content) > max_chars:
        content = content[: max_chars - 1].rstrip() + "…"
    return content


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

    report_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
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
