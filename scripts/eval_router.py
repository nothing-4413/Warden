"""评测用的极简 OpenAI 兼容路由：把两个本地实例拼成一个 base_url（纯标准库）。

Warden 的 `LLMClient` 与 `EmbeddingClient` 共用 `settings.llm_base_url`，
所以本地用 llama.cpp 跑「对话」和「嵌入」两个进程时，需要一个入口把两者拼起来：

    python -m scripts.eval_router --chat http://127.0.0.1:11500 --embed http://127.0.0.1:11501

之后 `WARDEN_LLM_BASE_URL=http://127.0.0.1:11434/v1` 就能同时走 chat 与 embeddings。
用 Ollama 时不需要它——11434 一个端口本来就同时提供两者。

转发一律绕开系统代理（`ProxyHandler({})`）：目标全是本机，而 Windows 上的系统代理
会把 127.0.0.1 也劫持成 502 空 body（原因与解法见 `app/net.py`）。
"""

from __future__ import annotations

import argparse
import http.server
import sys
import urllib.error
import urllib.request

# 逐跳头不能透传，转发时丢掉
_SKIP_HEADERS = frozenset(
    {"host", "content-length", "connection", "transfer-encoding", "accept-encoding"}
)
_TIMEOUT_S = 900.0
# 显式不带代理的 opener：目标是本机，走系统代理就是 502
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def make_handler(chat_url: str, embed_url: str) -> type[http.server.BaseHTTPRequestHandler]:
    """造一个把 chat / embeddings 分流的 Handler 类。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "warden-eval-router"

        def log_message(self, fmt: str, *args: object) -> None:
            sys.stderr.write("router: " + (fmt % args) + "\n")

        # ---- 内部 ----

        def _reply(
            self, status: int, payload: bytes, headers: dict[str, str] | None = None
        ) -> None:
            """回一个响应；headers 给了就透传（成功路径），否则自己补 Content-Type。"""
            self.send_response(status)
            if headers is None:
                self.send_header("Content-Type", "application/json")
            else:
                for key, value in headers.items():
                    if key.lower() not in _SKIP_HEADERS:
                        self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _forward(self, base: str) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            req = urllib.request.Request(base + self.path, data=body or None, method=self.command)
            for key, value in self.headers.items():
                if key.lower() not in _SKIP_HEADERS:
                    req.add_header(key, value)
            try:
                with _OPENER.open(req, timeout=_TIMEOUT_S) as resp:
                    self._reply(resp.status, resp.read(), dict(resp.headers))
            except urllib.error.HTTPError as exc:
                # 上游的 4xx/5xx 原样返回，别把客户端的判断吞掉。
                # HTTPError 自带一个要关闭的 body 流，不关会在解释器退出前
                # 触发 ResourceWarning（Implicitly cleaning up ...）。
                try:
                    payload = exc.read()
                finally:
                    exc.close()
                self._reply(exc.code, payload)
            except Exception as exc:
                payload = f'{{"error": "router: {type(exc).__name__}: {exc}"}}'.encode()
                self._reply(502, payload)

        # ---- HTTP 方法 ----

        def do_POST(self) -> None:
            if self.path.startswith("/v1/embeddings"):
                self._forward(embed_url)
            elif self.path.startswith("/v1/chat/completions"):
                self._forward(chat_url)
            else:
                self.send_error(404, "unknown path")

        def do_GET(self) -> None:
            if self.path.startswith("/v1/models"):
                self._forward(chat_url)
            elif self.path in ("/health", "/healthz"):
                self._reply(200, b"ok")
            else:
                self.send_error(404, "unknown path")

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把 chat / embeddings 两个本地实例拼成一个 base_url"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=11434, help="监听端口（默认 11434，即 Ollama 的端口）"
    )
    parser.add_argument("--chat", default="http://127.0.0.1:11500", help="对话实例地址")
    parser.add_argument("--embed", default="http://127.0.0.1:11501", help="嵌入实例地址")
    args = parser.parse_args(argv)

    server = http.server.ThreadingHTTPServer(
        (args.host, args.port), make_handler(args.chat, args.embed)
    )
    print(
        f"router on http://{args.host}:{args.port}  chat={args.chat}  embed={args.embed}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
