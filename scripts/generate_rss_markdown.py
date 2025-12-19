#!/usr/bin/env python3
import argparse
import json
import os
import re
import sys
from datetime import datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote_plus, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _child_text(el: ET.Element, name: str) -> Optional[str]:
    for child in el:
        if _local(child.tag) == name:
            return (child.text or "").strip()
    return None


def _parse_date(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text)
    except Exception:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None


def _open_url(url: str):
    parsed = urlparse(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) news-rss/1.0 (+https://github.com/antonmry/news)",
        "Accept": "application/xml,application/rss+xml,application/atom+xml;q=0.9,*/*;q=0.8",
    }
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if parsed.netloc.endswith("github.com") and token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, headers=headers)
    return urlopen(req)


def _fetch_bytes(url: str) -> Optional[bytes]:
    try:
        with _open_url(url) as resp:
            return resp.read()
    except HTTPError as e:
        reason = f"{e.code} {e.reason}".strip()
        print(f"warning: could not fetch {url} ({reason})", file=sys.stderr)
    except URLError as e:
        print(f"warning: could not fetch {url} ({e.reason})", file=sys.stderr)
    except Exception as e:
        print(f"warning: could not fetch {url}: {e}", file=sys.stderr)
    return None


def _clean_text(text: Optional[str]) -> str:
    if not text:
        return ""
    text = unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(text.split()).strip()


def _parse_list_url(list_url: str) -> Dict[str, str]:
    parsed = urlparse(list_url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 4 or parts[0] != "profile" or parts[2] != "lists":
        raise ValueError("List URL must look like https://bsky.app/profile/<handle>/lists/<list_id>")
    return {"handle": parts[1], "list_id": parts[3]}


def _resolve_handle(handle: str) -> str:
    url = f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={handle}"
    with _open_url(url) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    did = data.get("did")
    if not did:
        raise ValueError(f"Could not resolve handle: {handle}")
    return did


def _fetch_list_members(list_url: str) -> List[Dict[str, str]]:
    info = _parse_list_url(list_url)
    did = _resolve_handle(info["handle"])
    list_uri = f"at://{did}/app.bsky.graph.list/{info['list_id']}"
    api_url = f"https://public.api.bsky.app/xrpc/app.bsky.graph.getList?list={list_uri}"
    with _open_url(api_url) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    members: List[Dict[str, str]] = []
    for item in data.get("items", []):
        subject = item.get("subject") or {}
        subject_did = subject.get("did")
        handle = subject.get("handle")
        display_name = subject.get("displayName") or handle
        if not subject_did or not display_name:
            continue
        members.append(
            {
                "name": display_name,
                "url": f"https://bsky.app/profile/{subject_did}/rss",
            }
        )
    return members


def _load_github_repos(path: str) -> List[str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("GitHub JSON must be a list of repositories.")
    repos: List[str] = []
    for idx, item in enumerate(data):
        if isinstance(item, str):
            repo = item.strip()
        elif isinstance(item, dict):
            repo = str(item.get("repo", "")).strip()
        else:
            repo = ""
        if not repo or "/" not in repo:
            raise ValueError(f"Item {idx} must be a repo like owner/name.")
        repos.append(repo)
    return repos


def _parse_rss_items(root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    channel = None
    for child in root:
        if _local(child.tag) == "channel":
            channel = child
            break
    if channel is None:
        return items
    for item in channel:
        if _local(item.tag) != "item":
            continue
        title = _child_text(item, "title") or "Untitled"
        link = _child_text(item, "link") or ""
        pub_date = _parse_date(_child_text(item, "pubDate"))
        description = _child_text(item, "description")
        content = _child_text(item, "encoded")
        message = _clean_text(description or content or title) or "Untitled"
        items.append({"title": title, "message": message, "link": link, "date": pub_date})
    return items


def _parse_feed_title(root: ET.Element) -> Optional[str]:
    root_name = _local(root.tag)
    if root_name == "rss":
        for child in root:
            if _local(child.tag) == "channel":
                return _child_text(child, "title")
    if root_name == "feed":
        return _child_text(root, "title")
    return None


def _parse_atom_entries(root: ET.Element) -> List[Dict[str, Any]]:
    entries: List[Dict[str, Any]] = []
    for entry in root:
        if _local(entry.tag) != "entry":
            continue
        title = _child_text(entry, "title") or "Untitled"
        link = ""
        for link_el in entry:
            if _local(link_el.tag) != "link":
                continue
            rel = link_el.attrib.get("rel", "alternate")
            if rel == "alternate" and link_el.attrib.get("href"):
                link = link_el.attrib["href"]
                break
            if not link and link_el.attrib.get("href"):
                link = link_el.attrib["href"]
        date = _parse_date(_child_text(entry, "updated") or _child_text(entry, "published"))
        summary = _child_text(entry, "summary")
        content = _child_text(entry, "content")
        message = _clean_text(summary or content or title) or "Untitled"
        entries.append({"title": title, "message": message, "link": link, "date": date})
    return entries


def _parse_feed(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    root_name = _local(root.tag)
    if root_name == "rss":
        return _parse_rss_items(root)
    if root_name == "feed":
        return _parse_atom_entries(root)
    # Fallback: try to find items or entries anywhere
    items = root.findall(".//item")
    if items:
        return _parse_rss_items(root)
    return _parse_atom_entries(root)


def _parse_feed_with_title(xml_bytes: bytes) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    root = ET.fromstring(xml_bytes)
    title = _parse_feed_title(root)
    root_name = _local(root.tag)
    if root_name == "rss":
        return title, _parse_rss_items(root)
    if root_name == "feed":
        return title, _parse_atom_entries(root)
    items = root.findall(".//item")
    if items:
        return title, _parse_rss_items(root)
    return title, _parse_atom_entries(root)


def _yesterday_range_utc() -> Dict[str, datetime]:
    now_utc = datetime.now(tz=timezone.utc)
    yesterday = (now_utc - timedelta(days=1)).date()
    start = datetime.combine(yesterday, time.min, tzinfo=timezone.utc)
    end = datetime.combine(yesterday, time.max, tzinfo=timezone.utc)
    return {"start": start, "end": end}


def _filter_previous_day(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    window = _yesterday_range_utc()
    filtered: List[Dict[str, Any]] = []
    for entry in entries:
        dt = entry.get("date")
        if not isinstance(dt, datetime):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        utc_dt = dt.astimezone(timezone.utc)
        if window["start"] <= utc_dt <= window["end"]:
            filtered.append(entry)
    return filtered


def _latest_entry(entries: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not entries:
        return None
    sorted_entries = sorted(entries, key=lambda e: e.get("date") or datetime.min, reverse=True)
    return sorted_entries[0]


def _load_blog_feeds(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("Blogs JSON must be a list of feed URLs.")
    feeds: List[Dict[str, str]] = []
    for idx, item in enumerate(data):
        if isinstance(item, str):
            url = item.strip()
            name = ""
        elif isinstance(item, dict):
            url = str(item.get("url", "")).strip()
            name = str(item.get("name", "")).strip()
        else:
            url = ""
            name = ""
        if not url:
            raise ValueError(f"Blog item {idx} is missing a url.")
        feeds.append({"url": url, "name": name})
    return feeds


def _extract_youtube_channel_id(url: str) -> str:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "channel":
        return parts[1]
    html_bytes = _fetch_bytes(url)
    if not html_bytes:
        return ""
    html = html_bytes.decode("utf-8", "ignore")
    marker = '"channelId":"'
    idx = html.find(marker)
    if idx != -1:
        start = idx + len(marker)
        end = html.find('"', start)
        if end != -1:
            return html[start:end]
    import re

    m = re.search(r"(UC[a-zA-Z0-9_-]{20,})", html)
    if m:
        return m.group(1)
    return ""


def _format_link_pair(message: str, link: str, link_label: str, include_share: bool = True) -> str:
    if not link:
        return message
    if include_share:
        share = f"https://bsky.app/intent/compose?text={quote_plus(message + ' ' + link)}"
        return f"{message} [{link_label}]({link}) [Bsky]({share})"
    return f"{message} [{link_label}]({link})"


def _load_youtube_channels(path: str) -> List[Dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("YouTube JSON must be a list of channel URLs.")
    channels: List[Dict[str, str]] = []
    for idx, item in enumerate(data):
        if isinstance(item, str):
            url = item.strip()
            name = ""
        elif isinstance(item, dict):
            url = str(item.get("url", "")).strip()
            name = str(item.get("name", "")).strip()
        else:
            url = ""
            name = ""
        if not url:
            raise ValueError(f"YouTube item {idx} is missing a url.")
        channels.append({"url": url, "name": name})
    return channels


def generate_markdown(
    sources: List[Dict[str, str]],
    github_repos: List[str],
    blog_feeds: List[Dict[str, str]],
    youtube_channels: List[Dict[str, str]],
    report_date: str,
) -> str:
    lines: List[str] = []
    # Blogs
    blog_section: List[str] = []
    if blog_feeds:
        for feed in blog_feeds:
            xml_bytes = _fetch_bytes(feed["url"])
            if not xml_bytes:
                continue
            feed_title, entries = _parse_feed_with_title(xml_bytes)
            entries = _filter_previous_day(entries)
            if not entries:
                continue
            name = feed["name"] or feed_title or feed["url"]
            blog_section.append(f"### {name}")
            blog_section.append("")
            for entry in entries:
                message = entry.get("title") or entry.get("message") or "Untitled"
                link = entry.get("link") or ""
                blog_section.append(f"- {_format_link_pair(message, link, 'Article')}")
            blog_section.append("")
    if blog_section:
        lines.append("## Blogs")
        lines.append("")
        lines.extend(blog_section)

    # YouTube
    youtube_section: List[str] = []
    if youtube_channels:
        for channel in youtube_channels:
            channel_id = _extract_youtube_channel_id(channel["url"])
            if not channel_id:
                print(f"warning: skipping YouTube channel {channel['url']} (could not resolve id)", file=sys.stderr)
                continue
            feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            xml_bytes = _fetch_bytes(feed_url)
            if not xml_bytes:
                continue
            feed_title, entries = _parse_feed_with_title(xml_bytes)
            entries = _filter_previous_day(entries)
            if not entries:
                continue
            name = channel["name"] or feed_title or channel["url"]
            youtube_section.append(f"### {name}")
            youtube_section.append("")
            for entry in entries:
                message = entry.get("title") or entry.get("message") or "Untitled"
                link = entry.get("link") or ""
                youtube_section.append(f"- {_format_link_pair(message, link, 'Video')}")
            youtube_section.append("")
    if youtube_section:
        lines.append("## YouTube")
        lines.append("")
        lines.extend(youtube_section)

    # BlueSky
    bluesky_section: List[str] = []
    for source in sources:
        name = source["name"]
        url = source["url"]
        xml_bytes = _fetch_bytes(url)
        if not xml_bytes:
            continue
        entries = _parse_feed(xml_bytes)
        entries.sort(key=lambda e: e.get("date") or datetime.min, reverse=True)
        entries = _filter_previous_day(entries)
        if not entries:
            continue
        bluesky_section.append(f"### {name}")
        bluesky_section.append("")
        for entry in entries:
            message = entry.get("message") or entry.get("title") or "Untitled"
            link = entry.get("link") or ""
            bluesky_section.append(f"- {_format_link_pair(message, link, 'Post', include_share=False)}")
        bluesky_section.append("")
    if bluesky_section:
        lines.append("## BlueSky")
        lines.append("")
        lines.extend(bluesky_section)

    # GitHub Releases
    github_section: List[str] = []
    if github_repos:
        for repo in github_repos:
            feed_url = f"https://github.com/{repo}/releases.atom"
            xml_bytes = _fetch_bytes(feed_url)
            if not xml_bytes:
                continue
            entries = _parse_feed(xml_bytes)
            entries = _filter_previous_day(entries)
            if not entries:
                continue
            latest = _latest_entry(entries)
            if not latest:
                continue
            title = latest.get("title") or "Untitled"
            details = latest.get("message")
            link = latest.get("link") or f"https://github.com/{repo}/releases"
            if details and details != title:
                full_msg = f"{repo}: {title} — {details}"
            else:
                full_msg = f"{repo}: {title}"
            github_section.append(f"- {_format_link_pair(full_msg, link, 'Release')}")
    if github_section:
        lines.append("## GitHub Releases")
        lines.append("")
        lines.extend(github_section)

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Markdown (Obsidian) from a Bluesky list and GitHub releases."
    )
    parser.add_argument(
        "--list",
        required=True,
        help="Bluesky list URL like https://bsky.app/profile/<handle>/lists/<list_id>",
    )
    parser.add_argument(
        "--github-input",
        help="Path to JSON list of GitHub repositories like owner/name.",
    )
    parser.add_argument("--output", help="Output Markdown file path.")
    parser.add_argument(
        "--blogs-input",
        help="Path to JSON list of blog feed URLs.",
    )
    parser.add_argument(
        "--youtube-input",
        help="Path to JSON list of YouTube channel URLs.",
    )
    args = parser.parse_args()

    report_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    output_path = args.output or f"{report_date}.md"
    sources = _fetch_list_members(args.list)
    github_repos = _load_github_repos(args.github_input) if args.github_input else []
    blog_feeds = _load_blog_feeds(args.blogs_input) if args.blogs_input else []
    youtube_channels = _load_youtube_channels(args.youtube_input) if args.youtube_input else []
    markdown = generate_markdown(sources, github_repos, blog_feeds, youtube_channels, report_date)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
