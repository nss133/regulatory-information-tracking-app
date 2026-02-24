from __future__ import annotations

import warnings

warnings.filterwarnings(
    "ignore",
    message=r"urllib3 v2 only supports OpenSSL.*LibreSSL.*",
)

from briefing.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

