import argparse
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERSION = "1.14.2"
CHECKOUT = ROOT / ".runtime" / ("dify-" + VERSION)
DOCKER_DIR = CHECKOUT / "docker"
PROJECT = "fog-harbor-" + hashlib.sha256(str(ROOT).encode()).hexdigest()[:8]


def setup():
    if not CHECKOUT.exists():
        CHECKOUT.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "git", "clone", "--depth", "1", "--single-branch", "--branch", VERSION,
            "https://github.com/langgenius/dify.git", str(CHECKOUT),
        ], check=True)
    if not (DOCKER_DIR / "docker-compose.yaml").is_file():
        raise RuntimeError(f"Dify checkout is incomplete: {CHECKOUT}")
    env_file = DOCKER_DIR / ".env"
    if not env_file.exists():
        shutil.copyfile(DOCKER_DIR / ".env.example", env_file)


def check_docker():
    if not shutil.which("docker"):
        raise RuntimeError("Docker is required. Start a Linux Docker engine with Docker Compose.")
    result = subprocess.run(["docker", "compose", "version", "--short"], check=True, capture_output=True, text=True)
    match = re.match(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    if not match or tuple(map(int, match.groups())) < (2, 24, 4):
        raise RuntimeError("Docker Compose 2.24.4 or newer is required.")
    result = subprocess.run(["docker", "info", "--format", "{{.OSType}}"], check=True, capture_output=True, text=True)
    if result.stdout.strip() != "linux":
        raise RuntimeError("Switch Docker to Linux containers.")


def compose_args(port):
    env = os.environ.copy()
    env.update({
        "FOG_HARBOR_DIR": ROOT.as_posix(),
        "FOG_HARBOR_PORT": str(port),
        "COMPOSE_PROFILES": "weaviate,postgresql",
        "ENABLE_COLLABORATION_MODE": "false",
        "VECTOR_STORE": "weaviate",
        "DB_TYPE": "postgresql",
        "NGINX_PORT": "80",
    })
    command = [
        "docker", "compose", "--project-name", PROJECT, "--project-directory", str(DOCKER_DIR),
        "--env-file", str(DOCKER_DIR / ".env"),
        "-f", str(DOCKER_DIR / "docker-compose.yaml"), "-f", str(ROOT / "compose.story.yaml"),
    ]
    return command, env


def main():
    parser = argparse.ArgumentParser(description="Run the Fog Harbor Dify demo")
    parser.add_argument("command", choices=["up", "status", "logs", "restart-memory", "down"])
    parser.add_argument("--port", type=int, default=8088)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    commands = {
        "up": ["up", "-d", "--build", "--wait", "--wait-timeout", "300"],
        "status": ["ps", "-a"],
        "logs": ["logs", "--tail", "120", "story-memory"],
        "restart-memory": ["restart", "story-memory"],
        "down": ["down"],
    }
    try:
        check_docker()
        if args.command == "up":
            setup()
        elif not (DOCKER_DIR / ".env").is_file():
            raise RuntimeError("Run 'python run.py up' first.")
        command, env = compose_args(args.port)
        subprocess.run(command + commands[args.command], env=env, check=True)
        if args.command == "up":
            print(f"Dify setup: http://127.0.0.1:{args.port}/install")
            print(f"Import DSL: {ROOT / 'fog-harbor.yml'}")
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            print(exc.stderr.strip())
        parser.exit(1, f"Command failed with status {exc.returncode}.\n")
    except (OSError, RuntimeError) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
