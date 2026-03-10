"""OpenClaw – Telegram & Feishu notification service."""

import logging
import os
import signal
from http.server import BaseHTTPRequestHandler, HTTPServer

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler that exposes a /health endpoint."""

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, fmt, *args):  # silence default access logs
        pass


def main():
    port = int(os.getenv("APP_PORT", "8080"))
    logger.info("OpenClaw starting on port %d …", port)
    server = HTTPServer(("0.0.0.0", port), HealthHandler)

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down …", signum)
        server.shutdown()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    logger.info("Service ready. Listening on 0.0.0.0:%d", port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info("Server stopped.")


if __name__ == "__main__":
    main()
