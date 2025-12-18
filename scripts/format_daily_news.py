#!/usr/bin/env python3
import subprocess
from datetime import date, timedelta


def main() -> int:
    report_date = (date.today() - timedelta(days=1)).isoformat()
    filename = f"{report_date}.md"
    result = subprocess.run(
        ["uv", "tool", "run", "--from", "rumdl", "rumdl", "fmt", filename],
        check=False,
    )
    if result.returncode != 0:
        print("Warning: rumdl reported issues after formatting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
