"""Deprecated Streamlit Personal Care view.

Use the interactive dashboard instead:

    ./run_pc_dashboard

Or:

    .venv/bin/python scripts/export_pc_dashboard_data.py
    cd apps/ashfield-pc && npm run dev
"""

from __future__ import annotations

import sys


def main() -> int:
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
