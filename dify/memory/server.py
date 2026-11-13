import argparse
import hashlib
import json
import logging
import os
import re
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener


LOG = logging.getLogger("story-memory")
CODE = re.compile(r"H-[0-9A-F]{24}")
MAX_BODY = 4096
BOOT_SCOPE = {
    "title": "雾港留灯",
    "summary": "互动故事的值班簿。每条 Atomic Memory 的修订记录一次已确认的存档进度。",
    "idempotency_key": "fog-harbor-dify-saves-v1",
}


class PowerContext:
    def __init__(self, port, token):
        self.base = f"http://127.0.0.1:{port}"
        self.opener = build_opener(ProxyHandler({}))
        self.token = token
        self.scope = None
        self.write_lock = threading.Lock()

    def request(self, method, path, payload=None, *, etag=None):
        headers = {"Accept": "application/json", "Authorization": "Bearer " + self.token}
        if etag:
            headers["If-Match"] = etag
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=4) as response:
                return json.load(response), response.headers
        except HTTPError as exc:
            LOG.warning("powercontext method=%s path=%s status=%s", method, path, exc.code)
            exc.close()
            raise

    def prepare(self):
        descriptor, _ = self.request("POST", "/v1/scopes", BOOT_SCOPE)
        self.scope = descriptor["scope_id"]

    def lookup(self, kind):
        result, headers = self.request("POST", "/v1/atomic-memory/list", {
            "scope_id": self.scope,
            "kind": kind,
            "states": ["active"],
            "limit": 2,
        })
        records = result["items"]
        if len(records) > 1 or result.get("next_cursor"):
            raise ValueError("more than one active artifact for a save")
        if not records:
            return None
        record = records[0]
        if record["kind"] != kind:
            raise ValueError("unexpected Atomic Memory kind")
        return record, headers

    def audit(self, event, ref, headers):
        LOG.info(
            "powercontext event=%s scope=%s artifact=%s revision=%s request_id=%s",
            event, self.scope, ref["artifact_id"], ref["revision"],
            headers.get("X-PowerContext-Request-ID", ""),
        )

    def load(self, payload):
        op = payload.get("op")
        if op == "new":
            return {"status": "new", "code": "H-" + secrets.token_hex(12).upper()}
        if op not in {"resume", "move"}:
            return {"status": "skipped"}

        result = self.lookup(memory_kind(payload.get("code")))
        if result is None:
            return {"status": "missing"}
        record, headers = result
        ref = record["artifact"]
        self.audit("load", ref, headers)
        return {
            "status": "loaded", "state": json.loads(record["text"]),
            "version": ref["revision"],
        }

    def save(self, payload):
        kind = memory_kind(payload.get("code"))
        version, state = payload.get("version"), payload.get("state")
        if type(version) is not int or not 0 <= version < 3 or not isinstance(state, dict):
            return {"status": "invalid"}
        content = {
            "kind": kind,
            "text": json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        }
        base_path = "/v1/scopes/" + quote(self.scope, safe="") + "/artifacts"
        with self.write_lock:
            result = self.lookup(kind)
            if result is None:
                if version != 0:
                    return {"status": "conflict"}
                ref, headers = self.request("POST", base_path, {
                    "family": "atomic-memory", "content": content,
                })
                self.audit("create", ref, headers)
                return {"status": "saved"}

            record, headers = result
            ref = record["artifact"]
            if ref["revision"] == version + 1 and json.loads(record["text"]) == state:
                self.audit("retry", ref, headers)
                return {"status": "saved"}
            if version == 0 or ref["revision"] != version:
                return {"status": "conflict"}

            path = base_path + "/atomic-memory/" + quote(ref["artifact_id"], safe="")
            head, headers = self.request("GET", path)
            if head["revision"] != version:
                return {"status": "conflict"}
            try:
                updated, headers = self.request("PUT", path, {"content": content}, etag=headers["ETag"])
            except HTTPError as exc:
                if exc.code == 412:
                    return {"status": "conflict"}
                raise
            self.audit("update", updated, headers)
            return {"status": "saved"}


def memory_kind(code):
    if not isinstance(code, str) or not CODE.fullmatch(code):
        raise ValueError("invalid save code")
    return "fog-harbor-" + hashlib.sha256(code.encode("ascii")).hexdigest()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, status, result):
        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path != "/health":
            self.send_json(404, {"status": "missing"})
            return
        try:
            self.server.store.request("GET", "/v1/capabilities")
        except (OSError, ValueError):
            self.send_json(503, {"status": "unavailable"})
            return
        self.send_json(200, {"status": "ready"})

    def do_POST(self):
        routes = {"/load": self.server.store.load, "/save": self.server.store.save}
        operation = routes.get(self.path)
        if operation is None:
            self.send_json(404, {"status": "missing"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError("invalid request length")
            self.connection.settimeout(6)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("expected a JSON object")
        except (OSError, ValueError):
            self.send_json(400, {"status": "invalid"})
            return
        try:
            result = operation(payload)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            LOG.error("memory request path=%s error=%s", self.path, type(exc).__name__)
            result = {"status": "unavailable"}
        self.send_json(200, result)


def serve(args):
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    token = secrets.token_urlsafe(32)
    env.update({
        "POWERCONTEXT_HOME": str(data / "home"),
        "POWERCONTEXT_SERVER_DATABASE_KIND": "sqlite",
        "POWERCONTEXT_SERVER_DATABASE_URL": "sqlite+aiosqlite:///" + (data / "powercontext.db").as_posix(),
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": token,
    })
    backend = subprocess.Popen([
        args.powercontext, "server", "run", "--host", "127.0.0.1", "--port", str(args.powercontext_port),
    ], env=env, cwd=data)
    try:
        store = PowerContext(args.powercontext_port, token)
        deadline = time.monotonic() + 90
        while backend.poll() is None and time.monotonic() < deadline:
            try:
                store.prepare()
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError(f"PowerContext initialization failed (exit={backend.poll()})")

        with ThreadingHTTPServer((args.host, args.port), Handler) as http:
            http.store = store
            http.timeout = 1
            LOG.info("ready port=%s scope=%s", args.port, store.scope)
            while backend.poll() is None:
                http.handle_request()
            raise RuntimeError(f"PowerContext exited with status {backend.returncode}")
    finally:
        if backend.poll() is None:
            backend.terminate()
            try:
                backend.wait(timeout=10)
            except subprocess.TimeoutExpired:
                backend.kill()
                backend.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PowerContext storage for Fog Harbor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8079)
    parser.add_argument("--data", default="data")
    parser.add_argument("--powercontext", default="powercontext")
    parser.add_argument("--powercontext-port", type=int, default=8000)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    serve(parser.parse_args())
