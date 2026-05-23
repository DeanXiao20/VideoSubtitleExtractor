"""Unified launcher: starts Streamlit + reverse proxy on a single port."""
import os
import sys
import time
import socket
import subprocess
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import APP_PORT
_STREAMLIT_PORT = 8654


def _wait_for_port(port: int, timeout: float = 30) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def main() -> None:
    # Sync the internal port constant
    import src.share_server
    src.share_server._STREAMLIT_PORT = _STREAMLIT_PORT

    # Start Streamlit on internal port
    streamlit_proc = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "app.py",
            "--server.port", str(_STREAMLIT_PORT),
            "--server.headless", "true",
            "--browser.gatherUsageStats", "false",
        ],
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )

    print(f"Starting Streamlit on port {_STREAMLIT_PORT}...")
    if not _wait_for_port(_STREAMLIT_PORT):
        print("ERROR: Streamlit failed to start")
        streamlit_proc.kill()
        sys.exit(1)
    print("Streamlit is ready.")

    # Start unified proxy on public port
    from src.share_server import start_unified_server
    start_unified_server()
    print(f"Unified server running on port {APP_PORT}")
    print(f"  - Streamlit UI:  http://localhost:{APP_PORT}/")
    print(f"  - HTML files:    http://localhost:{APP_PORT}/output/<filename>.html")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
        streamlit_proc.terminate()
        streamlit_proc.wait(timeout=5)
        from src.share_server import stop_unified_server
        stop_unified_server()
        print("Stopped.")


if __name__ == "__main__":
    main()
