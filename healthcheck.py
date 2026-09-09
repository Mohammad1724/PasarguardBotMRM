"""Local-only readiness probe; stdlib only, no credentials or network API exposed."""

import socket
import sys
from pathlib import Path

SOCKET_PATH = Path("logs/.readiness.sock")


def probe(path=SOCKET_PATH, timeout=5):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            # Read to EOF: a partial/oversized/unrecognized response is not readiness.
            response = b""
            while len(response) < 32:
                part = client.recv(32)
                if not part:
                    return response == b"ready\n"
                response += part
    except OSError, AttributeError:
        pass
    return False


if __name__ == "__main__":
    ready = probe()
    print("ready" if ready else "not ready")
    sys.exit(0 if ready else 1)
