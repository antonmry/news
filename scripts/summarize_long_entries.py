#!/usr/bin/env python3
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
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

# Maximum input length to send to the API (to prevent timeouts with extremely long texts)
MAX_INPUT_LENGTH = 10000
# Maximum number of retries for API calls
MAX_RETRIES = 3
# Initial retry delay in seconds
RETRY_DELAY = 2
# API call timeout in seconds
API_TIMEOUT = 60


def _split_link(line: str) -> Tuple[str, str]:
    idx = line.rfind("](")
    if idx == -1:
        return line, ""
    start = line.rfind(" [", 0, idx)
    if start == -1:
        return line, ""
    return line[:start], line[start:]


def _call_api_with_timeout(client, messages, model):
    """Helper function to make API call with timeout using thread executor."""
    try:
        response = client.complete(
            messages=messages,
            model=model,
        )
        return response
    except Exception as e:
        raise


def _call_github_models(prompt: str, max_chars: int) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_MODELS_TOKEN.")
    model = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-5-nano")
    endpoint = os.environ.get("GITHUB_MODELS_ENDPOINT", "https://models.github.ai/inference")

    # Truncate input if it's too long to prevent timeouts
    if len(prompt) > MAX_INPUT_LENGTH:
        prompt = prompt[:MAX_INPUT_LENGTH] + "..."
        print(f"Warning: Input truncated to {MAX_INPUT_LENGTH} characters")

    client = ChatCompletionsClient(
        endpoint=endpoint,
        credential=AzureKeyCredential(token),
    )

    messages = [
        SystemMessage(
            "Summarize the text to fit within the requested character limit. "
            "Preserve key details, names, and links mentioned in the text. "
            "Return plain text only."
        ),
        UserMessage(f"Limit: {max_chars} characters.\nText: {prompt}"),
    ]

    # Retry logic with exponential backoff
    for attempt in range(MAX_RETRIES):
        try:
            # Use ThreadPoolExecutor to enforce timeout
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call_api_with_timeout, client, messages, model)
                try:
                    response = future.result(timeout=API_TIMEOUT)
                except FuturesTimeoutError:
                    raise TimeoutError(f"API call timed out after {API_TIMEOUT} seconds")

            content = (response.choices[0].message.content or "").strip()
            if len(content) > max_chars:
                content = content[: max_chars - 1].rstrip() + "…"
            return content
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAY * (2 ** attempt)
                print(f"API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. Retrying in {delay}s...")
                time.sleep(delay)
            else:
                print(f"API call failed after {MAX_RETRIES} attempts: {e}")
                raise

    return ""


def _summarize_line(line: str, max_chars: int) -> str:
    if not line.startswith("- "):
        return line
    message, link_part = _split_link(line)
    text = message[2:].strip()
    if len(text) <= max_chars:
        return line
    
    try:
        summary = _call_github_models(text, max_chars)
        if not summary:
            print(f"Warning: Empty summary returned, keeping original line")
            return line
        return f"- {summary}{link_part}"
    except Exception as e:
        print(f"Error summarizing line: {e}. Keeping original line.")
        return line


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize long markdown entries via GitHub Models.")
    parser.add_argument("--file", help="Markdown file to process.")
    parser.add_argument("--max-chars", type=int, default=300, help="Max characters per entry.")
    args = parser.parse_args()

    report_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    path = args.file or f"{report_date}.md"

    print(f"Processing file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    print(f"Total lines: {len(lines)}")
    updated = []
    lines_summarized = 0
    for i, line in enumerate(lines):
        if line.startswith("- "):
            message, _ = _split_link(line)
            text = message[2:].strip()
            if len(text) > args.max_chars:
                print(f"Summarizing line {i + 1}: {len(text)} chars -> {args.max_chars} chars")
                lines_summarized += 1
        
        updated.append(_summarize_line(line, args.max_chars))

    print(f"Lines summarized: {lines_summarized}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
