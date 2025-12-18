#!/usr/bin/env python3
import subprocess
from datetime import date, timedelta


def main() -> int:
    report_date = (date.today() - timedelta(days=1)).isoformat()
    filename = f"{report_date}.md"
    subprocess.run(
        ["uv", "tool", "run", "--from", "rumdl", "rumdl", "--fix", filename],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
