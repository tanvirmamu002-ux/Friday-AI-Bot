import os
import logging
import threading
from flask import Flask, jsonify

log = logging.getLogger(__name__)

_flask_app = Flask(__name__)


@_flask_app.route("/")
def index():
    return "✅ Friday AI Bot is alive!", 200


@_flask_app.route("/health")
def health():
    return jsonify({"status": "ok", "bot": "Friday AI", "owner": "@hm_burhan"}), 200


@_flask_app.route("/ping")
def ping():
    return "pong", 200


def start(port: int | None = None):
    """Start Flask keep-alive server in a daemon thread."""
    if port is None:
        port = int(os.environ.get("PORT", 5000))

    import logging as _lg
    _lg.getLogger("werkzeug").setLevel(_lg.ERROR)

    t = threading.Thread(
        target=lambda: _flask_app.run(host="0.0.0.0", port=port),
        daemon=True,
        name="flask-keep-alive",
    )
    t.start()
    log.info(f"Flask keep-alive started on port {port} (routes: / /health /ping)")


if __name__ == "__main__":
    # Standalone mode: run Flask in foreground (used by artifact dev workflow)
    import logging as _lg
    _lg.basicConfig(level=_lg.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    _lg.getLogger("werkzeug").setLevel(_lg.ERROR)
    port = int(os.environ.get("PORT", 5000))
    log.info(f"Keep-alive server starting on port {port}")
    _flask_app.run(host="0.0.0.0", port=port)
