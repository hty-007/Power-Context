"""Import and verify the app on a freshly deployed, disposable Dify instance."""

import base64
import json
import secrets
import subprocess
import time
from datetime import datetime, timezone
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

from run import ROOT, compose_args
from verify import check_dify, expect


PORT = 8088
BASE = f"http://127.0.0.1:{PORT}"
COOKIES = CookieJar()
OPENER = build_opener(HTTPCookieProcessor(COOKIES))


def console(path, payload=None):
    headers = {"Accept": "application/json"}
    token = next((cookie.value for cookie in COOKIES if cookie.name == "csrf_token"), None)
    if token:
        headers["X-CSRF-Token"] = token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = Request(BASE + "/console/api" + path, data=data, headers=headers)
    try:
        with OPENER.open(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as exc:
        exc.close()
        raise RuntimeError(f"Dify console {path} returned HTTP {exc.code}") from None


def wait_for_setup():
    deadline = time.monotonic() + 150
    last_error = None
    while time.monotonic() < deadline:
        try:
            return console("/setup")
        except (RuntimeError, OSError, ValueError) as exc:
            last_error = exc
            time.sleep(3)
    raise RuntimeError(f"Dify setup endpoint did not become ready: {last_error}")


def bootstrap():
    step = wait_for_setup().get("step")
    expect(step == "not_started", "clean_acceptance.py expects a fresh Dify installation")
    email = "fog-harbor-test@example.com"
    password = "Aa1!" + secrets.token_urlsafe(10)
    created = console("/setup", {"email": email, "name": "Fog Harbor QA", "password": password, "language": "en-US"})
    expect(created.get("result") == "success", "Dify administrator setup succeeded")
    login = console("/login", {"email": email, "password": base64.b64encode(password.encode()).decode()})
    expect(login.get("result") == "success", "Dify administrator login succeeded")

    imported = console("/apps/imports", {
        "mode": "yaml-content", "yaml_content": (ROOT / "fog-harbor.yml").read_text(encoding="utf-8"),
    })
    expect(imported.get("status") == "completed", f"DSL import result: {imported}")
    app_id = imported["app_id"]
    published = console(f"/apps/{app_id}/workflows/publish", {})
    expect(published.get("result") == "success", "workflow was published in the disposable environment")
    return console(f"/apps/{app_id}/api-keys", {})["token"]


def run_compose(*args):
    cmd, env = compose_args(PORT)
    return subprocess.run(cmd + list(args), env=env, check=True, capture_output=True, text=True).stdout


def restart_memory():
    run_compose("restart", "story-memory")
    run_compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "story-memory")
    print("PowerContext container restarted before cross-conversation resume", flush=True)


def verify_storage():
    logs = run_compose(
        "exec", "-T", "story-memory", "python", "-c",
        "import sys; sys.path.insert(0, '/tmp'); from fog_harbor_verify import check_storage; "
        "check_storage('http://127.0.0.1:8079')",
    )
    print(logs.strip(), flush=True)


def main():
    token = bootstrap()
    print("Dify 1.14.2 imported the DSL and published the Chatflow", flush=True)
    turns = check_dify(BASE, token, restart_memory=restart_memory)
    report = {
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "dify": "1.14.2", "powercontext": "1.4.1", "memory_container_restarted": True,
        "turn_count": len(turns), "turns": turns,
    }
    output = ROOT / "acceptance.local.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{len(turns)} live turns passed across independent Dify conversations", flush=True)
    for turn in turns:
        heading = next((line[4:] for line in turn["answer"].splitlines() if line.startswith("### ")), "")
        first_line = turn["answer"].splitlines()[0]
        print(f"{turn['query']} | {turn['conversation_id']} | {heading or first_line}", flush=True)
    run_compose("cp", str(ROOT / "verify.py"), "story-memory:/tmp/fog_harbor_verify.py")
    verify_storage()


if __name__ == "__main__":
    main()
