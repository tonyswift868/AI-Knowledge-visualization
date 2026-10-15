"""Run the production Python app over test-local HTTPS for the 147 contracts."""
from __future__ import annotations

import os
import uvicorn
from refactor.server.app import app


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["PORT"]),
        ssl_certfile=os.environ["CONTRACT147_SERVER_CERT"],
        ssl_keyfile=os.environ["CONTRACT147_SERVER_KEY"],
        log_level="error", access_log=False, lifespan="on", proxy_headers=False)
