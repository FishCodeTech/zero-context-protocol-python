#!/usr/bin/env python3
"""Official ASGI host runner for the ZCP backend template."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.zcp_server_template import application


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    log_level = os.environ.get("LOG_LEVEL", "warning")

    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError("uvicorn is required to run the API server") from exc

    uvicorn.run(
        application,
        host=host,
        port=port,
        log_level=log_level,
        ws="auto",
        lifespan="off",
    )


if __name__ == "__main__":
    main()
