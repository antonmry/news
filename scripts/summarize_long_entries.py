#!/usr/bin/env python3
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timedelta, timezone
from typing import Tuple
from urllib.request import Request, urlopen
from urllib.error import HTTPError

# Maximum input length to send to the API (to prevent timeouts with extremely long texts)
MAX_INPUT_LENGTH = int(os.environ.get("SUMMARY_MAX_INPUT", "10000"))
# Maximum number of retries for API calls
MAX_RETRIES = int(os.environ.get("SUMMARY_MAX_RETRIES", "3"))
# Initial retry delay in seconds
RETRY_DELAY = int(os.environ.get("SUMMARY_RETRY_DELAY", "2"))
# Minimum delay between calls to reduce rate-limit errors
MIN_CALL_INTERVAL = float(os.environ.get("SUMMARY_MIN_INTERVAL", "1.0"))
# API call timeout in seconds
API_TIMEOUT = int(os.environ.get("SUMMARY_TIMEOUT", "60"))
# Maximum number of summaries per run to avoid runaway jobs
MAX_SUMMARIES = int(os.environ.get("SUMMARY_MAX_CALLS", "50"))
# Explicit User-Agent to keep GitHub Models happy
USER_AGENT = os.environ.get("SUMMARY_USER_AGENT", "news-summarizer/1.0")


def _wrap_summary(text: str) -> str:
    """Return a Markdown-safe, quoted summary string."""
    if not text:
        return text
    clean = " ".join(text.split())  # collapse whitespace/newlines
    clean = clean.replace('"', "'").strip()
    return f"\"{clean}\""


def _split_link(line: str) -> Tuple[str, str]:
    idx = line.rfind("](")
    if idx == -1:
        return line, ""
    start = line.rfind(" [", 0, idx)
    if start == -1:
        return line, ""
    return line[:start], line[start:]


def _call_api(prompt: str, max_chars: int, token: str, endpoint: str, model: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                "Summarize the text to fit within the requested character limit. "
                    "Preserve key details, names, and links mentioned in the text. "
                    "Return a single line of plain text without Markdown formatting or bullet markers. "
                    "Do not add newlines or headings. Keep URLs untouched."
                ),
            },
            {"role": "user", "content": f"Limit: {max_chars} characters.\nText: {prompt}"},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2023-07-07",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=API_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return content.strip()
    except HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "ignore")
        except Exception:
            detail = ""
        msg = f"{e.code} {e.reason}".strip()
        if detail:
            msg = f"{msg}: {detail}"
        raise RuntimeError(msg) from e


def _call_with_timeout(prompt: str, max_chars: int, token: str, endpoint: str, model: str) -> str:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_call_api, prompt, max_chars, token, endpoint, model)
        try:
            return future.result(timeout=API_TIMEOUT)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise TimeoutError(f"API call timed out after {API_TIMEOUT} seconds") from exc


def _call_github_models(prompt: str, max_chars: int) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GITHUB_MODELS_TOKEN")
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN or GITHUB_MODELS_TOKEN.")
    model = os.environ.get("GITHUB_MODELS_MODEL", "openai/gpt-4.1")
    endpoint = os.environ.get(
        "GITHUB_MODELS_ENDPOINT",
        "https://models.github.ai/inference/v1/chat/completions",
    )

    if len(prompt) > MAX_INPUT_LENGTH:
        prompt = prompt[:MAX_INPUT_LENGTH] + "..."
        print(f"[summarize] Warning: Input truncated to {MAX_INPUT_LENGTH} characters", flush=True)

    # Simple pacing to avoid hammering the endpoint
    if MIN_CALL_INTERVAL > 0:
        now = time.monotonic()
        last = _call_github_models._last_call  # type: ignore[attr-defined]
        elapsed = now - last if last else None
        if elapsed is not None and elapsed < MIN_CALL_INTERVAL:
            sleep_for = MIN_CALL_INTERVAL - elapsed
            time.sleep(sleep_for)
    _call_github_models._last_call = time.monotonic()  # type: ignore[attr-defined]

    for attempt in range(MAX_RETRIES):
        try:
            content = _call_with_timeout(prompt, max_chars, token, endpoint, model)
            if len(content) > max_chars:
                content = content[: max_chars - 1].rstrip() + "…"
            return content
        except Exception as e:
            is_last = attempt >= MAX_RETRIES - 1
            # crude check for rate limit; backoff and retry
            if not is_last and ("429" in str(e) or "rate" in str(e).lower()):
                retry_after = getattr(e, "headers", None)
                delay = None
                if retry_after:
                    try:
                        delay = int(retry_after.get("Retry-After", "0"))
                    except Exception:
                        delay = None
                if delay is None or delay <= 0:
                    delay = RETRY_DELAY * (2 ** attempt)
                print(f"[summarize] Rate limited (attempt {attempt + 1}/{MAX_RETRIES}): {e}. Backing off {delay}s...", flush=True)
                time.sleep(delay)
                continue
            if not is_last:
                delay = RETRY_DELAY * (2 ** attempt)
                print(f"[summarize] API call failed (attempt {attempt + 1}/{MAX_RETRIES}): {e}. Retrying in {delay}s...", flush=True)
                time.sleep(delay)
            else:
                print(f"[summarize] API call failed after {MAX_RETRIES} attempts: {e}", flush=True)
                raise


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
            print("[summarize] Empty summary returned, keeping original line", flush=True)
            return line
        wrapped = _wrap_summary(summary)
        return f"- {wrapped}{link_part}"
    except Exception as e:
        print(f"[summarize] Error summarizing line: {e}. Keeping original line.", flush=True)
        return line


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize long markdown entries via GitHub Models.")
    parser.add_argument("--file", help="Markdown file to process.")
    parser.add_argument("--max-chars", type=int, default=300, help="Max characters per entry.")
    args = parser.parse_args()

    report_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    path = args.file or f"{report_date}.md"

    print(
        f"[summarize] Config: file={path}, max_chars={args.max_chars}, "
        f"timeout={API_TIMEOUT}s, max_calls={MAX_SUMMARIES}, max_input={MAX_INPUT_LENGTH}, retries={MAX_RETRIES}, min_interval={MIN_CALL_INTERVAL}s",
        flush=True,
    )
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()

    print(f"[summarize] Total lines: {len(lines)}", flush=True)
    updated = []
    lines_summarized = 0
    max_calls = MAX_SUMMARIES
    for i, line in enumerate(lines):
        if line.startswith("- "):
            message, _ = _split_link(line)
            text = message[2:].strip()
            if len(text) > args.max_chars:
                print(f"[summarize] Summarizing line {i + 1}: {len(text)} -> {args.max_chars} chars", flush=True)
                if lines_summarized >= max_calls:
                    print(f"[summarize] Reached max summaries ({max_calls}), skipping the rest.", flush=True)
                    updated.append(line)
                    continue
                lines_summarized += 1
                updated.append(_summarize_line(line, args.max_chars))
            else:
                updated.append(line)
        else:
            updated.append(line)

    print(f"[summarize] Lines summarized: {lines_summarized}", flush=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(updated).rstrip() + "\n")
    return 0


if __name__ == "__main__":
    # Track last call timestamp for rate limiting
    _call_github_models._last_call = 0.0  # type: ignore[attr-defined]
    raise SystemExit(main())
