"""Production Python app over HTTP behind the test HTTPS proxy for 149."""
from __future__ import annotations

import os

import uvicorn
from refactor.server.app import app


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]),
                log_level="error", access_log=False, lifespan="on",
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
