"""Check local ancestry without claiming GitHub access or repository ownership."""

import os
import subprocess
import tomllib
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    candidate = Path(os.environ.get("POWERCONTEXT_CANDIDATE", root.parent))
    baseline = tomllib.loads((root / "baseline.toml").read_text())["commit"]
    references = subprocess.run(
        [
            "git",
            "-C",
            str(candidate),
            "for-each-ref",
            "--contains",
            baseline,
            "--format=%(refname)",
            "refs/heads",
            "refs/remotes",
        ],
        capture_output=True,
        text=True,
    )
    head = subprocess.run(
        ["git", "-C", str(candidate), "merge-base", "--is-ancestor", baseline, "HEAD"],
        capture_output=True,
    )
    if head.returncode == 0 or (references.returncode == 0 and references.stdout.strip()):
        print("Frozen baseline is reachable from HEAD or a fetched branch.")
        print("GitHub access and repository origin are checked separately when submitting.")
        return 0
    print("No local ancestry proof found. Fetch the relevant branch history and check again.")
    print("An official GitHub fork may satisfy the alternative repository-origin requirement.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
