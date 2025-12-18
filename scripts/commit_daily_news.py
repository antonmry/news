#!/usr/bin/env python3
import subprocess
from datetime import datetime, timedelta, timezone


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def main() -> int:
    report_date = (datetime.now(tz=timezone.utc).date() - timedelta(days=1)).isoformat()
    filename = f"{report_date}.md"

    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])

    run(["git", "add", filename])
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        print("No changes to commit.")
        return 0

    run(["git", "commit", "-m", f"Add daily news for {filename}"])
    run(["git", "push"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
