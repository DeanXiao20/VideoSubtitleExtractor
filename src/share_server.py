import io
import socket
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

from config import APP_PORT, OUTPUT_DIR

_STREAMLIT_PORT = 8654
_FW_RULE_NAME = "VideoSubtitleShare"


def _get_local_ip() -> str:
    try:
        hostname = socket.gethostname()
        ips = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for _, _, _, _, addr in ips:
            ip = addr[0]
            if not ip.startswith("127.") and not ip.startswith("172."):
                return ip
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip.startswith("172."):
            result = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.splitlines():
                if "IPv4" in line:
                    addr = line.split(":")[-1].strip()
                    if not addr.startswith("127.") and not addr.startswith("172."):
                        return addr
        return ip
    except Exception:
        return "127.0.0.1"


def _ensure_firewall_rule() -> None:
    try:
        subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name={_FW_RULE_NAME}",
                "dir=in", "action=allow", "protocol=tcp",
                f"localport={APP_PORT}",
            ],
            capture_output=True, timeout=5,
            encoding="gbk", errors="ignore",
        )
    except Exception:
        pass


def _get_video_edits(video_id_str: str) -> dict | None:
    try:
        from src.database import Database
        db = Database()
        row = db.conn.execute(
            "SELECT title, url FROM videos WHERE video_id = ?", (video_id_str,)
        ).fetchone()
        db.close()
        if not row:
            return None
        return {"title": row["title"], "url": row["url"]}
    except Exception:
        return None


def _tcp_relay(src: socket.socket, dst: socket.socket) -> None:
    """Relay data from src to dst until connection closes."""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class _UnifiedHandler(BaseHTTPRequestHandler):
    """Routes /output/* to static files, WebSocket upgrades to TCP tunnel,
    everything else to Streamlit via HTTP proxy."""

    protocol_version = "HTTP/1.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/output/"):
            self._serve_static(path)
        elif self._is_websocket_upgrade():
            self._proxy_websocket()
        else:
            self._proxy_streamlit()

    def do_POST(self):
        if self._is_websocket_upgrade():
            self._proxy_websocket()
        else:
            self._proxy_streamlit()

    def do_OPTIONS(self):
        self._proxy_streamlit()

    def _is_websocket_upgrade(self) -> bool:
        return self.headers.get("Upgrade", "").lower() == "websocket"

    def _serve_static(self, path: str) -> None:
        filename = path.split("/output/", 1)[-1]
        safe_name = Path(filename).name
        if not safe_name.endswith(".html"):
            self.send_response(403)
            self.end_headers()
            return

        file_path = OUTPUT_DIR / safe_name
        if not file_path.exists():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        content = file_path.read_bytes()
        html_str = content.decode("utf-8")

        # Apply latest edits from database
        video_id_str = safe_name.replace(".html", "")
        updated = _get_video_edits(video_id_str)
        if updated:
            import re
            import html as html_mod
            new_title = updated.get("title")
            new_url = updated.get("url")
            if new_title:
                escaped = html_mod.escape(new_title)
                # Replace h1 title (with or without id)
                html_str = re.sub(
                    r'(<h1\s+class="video-title"[^>]*>)[^<]*(</h1>)',
                    rf'\g<1>{escaped}\g<2>',
                    html_str,
                )
                # Replace page title
                html_str = re.sub(
                    r'(<title>)[^<]* - 双语字幕(</title>)',
                    rf'\g<1>{escaped} - 双语字幕\g<2>',
                    html_str,
                )
            if new_url:
                escaped_url = html_mod.escape(new_url)
                # Replace source URL (with or without id)
                html_str = re.sub(
                    r'(<div\s+class="video-url"[^>]*>来源: )[^<]*(</div>)',
                    rf'\g<1>{escaped_url}\g<2>',
                    html_str,
                )
            content = html_str.encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(content)

    def _proxy_websocket(self) -> None:
        """Raw TCP tunnel for WebSocket connections."""
        try:
            backend = socket.create_connection(
                ("127.0.0.1", _STREAMLIT_PORT), timeout=30
            )
        except OSError:
            self.send_response(502)
            self.end_headers()
            return

        # Reconstruct and forward the original upgrade request
        req = f"{self.command} {self.path} HTTP/1.1\r\n"
        for key, value in self.headers.items():
            req += f"{key}: {value}\r\n"
        req += "\r\n"
        backend.sendall(req.encode())

        self.close_connection = True

        # Bidirectional relay using threads (more reliable than select on Windows)
        client = self.connection
        t1 = threading.Thread(target=_tcp_relay, args=(client, backend), daemon=True)
        t2 = threading.Thread(target=_tcp_relay, args=(backend, client), daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=300)
        t2.join(timeout=300)
        backend.close()

    def _proxy_streamlit(self) -> None:
        import http.client
        try:
            conn = http.client.HTTPConnection(
                "127.0.0.1", _STREAMLIT_PORT, timeout=30
            )
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            fwd_headers = {}
            for k, v in self.headers.items():
                if k.lower() not in ("host", "transfer-encoding"):
                    fwd_headers[k] = v
            conn.request(self.command, self.path, body=body, headers=fwd_headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                lower = k.lower()
                if lower in ("transfer-encoding",):
                    continue
                self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            conn.close()
        except Exception:
            try:
                self.send_response(502)
                self.end_headers()
            except Exception:
                pass

    def log_message(self, format, *args):
        pass


_proxy_server = None


def start_unified_server() -> None:
    global _proxy_server
    if _proxy_server is not None:
        return

    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(("0.0.0.0", APP_PORT), _UnifiedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    threading.Thread(target=_ensure_firewall_rule, daemon=True).start()

    _proxy_server = server


def stop_unified_server() -> None:
    global _proxy_server
    if _proxy_server is not None:
        try:
            _proxy_server.shutdown()
        except Exception:
            pass
        _proxy_server = None


def generate_frpc_config(public_url: str) -> str:
    parsed = urlparse(public_url)
    host = parsed.hostname or "your-server.com"

    return f"""# frpc.toml - 将本地 {APP_PORT} 端口映射到外网
# 用法: frpc -c frpc.toml

serverAddr = "{host}"
serverPort = 7000

[[proxies]]
name = "subtitle-tcp"
type = "tcp"
localIP = "127.0.0.1"
localPort = {APP_PORT}
remotePort = 18765
"""


def generate_qr_code(data: str) -> bytes:
    import qrcode

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#2c3e50", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
