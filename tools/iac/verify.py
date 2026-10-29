"""One command that climbs the IaC confidence ladder (docs/IAC-CONFIDENCE.md) in containers.

    python tools/iac/verify.py              # pin, fmt, validate, test
    python tools/iac/verify.py --scan       # also trivy config (HIGH and CRITICAL fail)
    python tools/iac/verify.py --tf 1.17    # Terraform image tag

Needs Docker. It never applies anything and leaves the working tree as it found it (the lock files
and `.terraform` folders that `init` creates are removed or restored afterwards).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TF_ROOT = ROOT / "infra" / "terraform"
MODULE = TF_ROOT / "modules" / "nodo"
ROOTS = [TF_ROOT / "envs" / "dev", TF_ROOT / "envs" / "prod", MODULE]


def pins(directory: Path) -> list[str]:
    """Rung 1: Terraform and every provider must carry a version constraint."""
    found = []
    for path in sorted(directory.glob("*.tf")):
        text = path.read_text(encoding="utf-8")
        for block in re.finditer(r"^terraform\s*\{", text, re.MULTILINE):
            body = text[block.end() :]
            body = body[: body.find("\n}\n")] if "\n}\n" in body else body
            if "required_version" not in body:
                found.append(f"{path.name}: terraform {{}} without required_version")
            for name, spec in re.findall(r"(\w+)\s*=\s*\{([^{}]*)\}", body):
                if "source" in spec and "version" not in spec:
                    found.append(
                        f"{path.name}: provider {name} has no version constraint"
                    )
    return found


def docker(
    image: str, args: list[str], workdir: Path, mount: Path = TF_ROOT
) -> subprocess.CompletedProcess[str]:
    relative = workdir.relative_to(mount).as_posix()
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{mount}:/work",
        "-w",
        f"/work/{relative}",
        image,
        *args,
    ]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        env={**os.environ, "MSYS_NO_PATHCONV": "1"},
    )


def tail(output: str, lines: int = 12) -> str:
    return "\n".join(output.strip().splitlines()[-lines:])


class Ladder:
    def __init__(self, terraform_tag: str) -> None:
        self.image = f"hashicorp/terraform:{terraform_tag}"
        self.results: list[tuple[str, bool, float]] = []

    def rung(self, name: str, action: Callable[[], tuple[bool, str]]) -> bool:
        started = time.monotonic()
        ok, detail = action()
        self.results.append((name, ok, time.monotonic() - started))
        print(
            f"{'PASS' if ok else 'FAIL'}  {name}  ({time.monotonic() - started:.0f}s)"
        )
        if not ok and detail:
            print("      " + detail.replace("\n", "\n      "))
        return ok

    def pin(self) -> tuple[bool, str]:
        problems = [f"{d.relative_to(ROOT)}/{p}" for d in ROOTS for p in pins(d)]
        envs = [
            d for d in ROOTS if d != MODULE and not (d / ".terraform.lock.hcl").exists()
        ]
        problems += [
            f"{d.relative_to(ROOT)}: no committed .terraform.lock.hcl" for d in envs
        ]
        return not problems, "\n".join(problems)

    def fmt(self) -> tuple[bool, str]:
        out = docker(self.image, ["fmt", "-check", "-recursive", "-diff"], TF_ROOT)
        return out.returncode == 0, tail(out.stdout + out.stderr, 25)

    def validate(self) -> tuple[bool, str]:
        for directory in ROOTS:
            for step in (
                ["init", "-backend=false", "-input=false", "-no-color"],
                ["validate", "-no-color"],
            ):
                out = docker(self.image, step, directory)
                if out.returncode:
                    return (
                        False,
                        f"{directory.relative_to(ROOT)}: terraform {step[0]}\n{tail(out.stdout + out.stderr)}",
                    )
        return True, ""

    def test(self) -> tuple[bool, str]:
        out = docker(self.image, ["test", "-no-color"], MODULE)
        return out.returncode == 0, tail(out.stdout + out.stderr, 20)

    def scan(self) -> tuple[bool, str]:
        out = docker(
            "aquasec/trivy:latest",
            [
                "config",
                "--severity",
                "HIGH,CRITICAL",
                "--exit-code",
                "1",
                "--quiet",
                ".",
            ],
            TF_ROOT,
        )
        return out.returncode == 0, tail(out.stdout + out.stderr, 30)


def clean() -> None:
    """Remove what `init` leaves behind; restore lock files that are tracked."""
    for directory in ROOTS:
        shutil.rmtree(directory / ".terraform", ignore_errors=True)
        lock = directory / ".terraform.lock.hcl"
        tracked = (
            subprocess.run(
                ["git", "ls-files", "--error-unmatch", str(lock)],
                cwd=ROOT,
                capture_output=True,
                check=False,
            ).returncode
            == 0
        )
        if tracked:
            subprocess.run(
                ["git", "checkout", "--", str(lock)],
                cwd=ROOT,
                capture_output=True,
                check=False,
            )
        else:
            lock.unlink(missing_ok=True)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--scan", action="store_true", help="also run trivy config")
    parser.add_argument("--tf", default="1.17", help="hashicorp/terraform image tag")
    args = parser.parse_args()
    if shutil.which("docker") is None:
        print("Docker is required.", file=sys.stderr)
        return 2
    ladder = Ladder(args.tf)
    try:
        green = ladder.rung("1 pin: versions and lock files", ladder.pin)
        green = ladder.rung("2 static: terraform fmt", ladder.fmt) and green
        green = (
            ladder.rung("3 static: terraform validate (every root)", ladder.validate)
            and green
        )
        green = ladder.rung("4 plan-time: terraform test", ladder.test) and green
        if args.scan:
            green = ladder.rung("5 security: trivy config", ladder.scan) and green
    finally:
        clean()
    print(
        "\nLadder:",
        "GREEN" if green else "RED",
        "- nothing was applied; rungs 5-8 need a real account (docs/IAC-CONFIDENCE.md)",
    )
    return 0 if green else 1


if __name__ == "__main__":
    raise SystemExit(main())
