import os
import subprocess
import sys


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "api"
    _run([sys.executable, "-m", "alembic", "upgrade", "head"])
    if mode == "worker":
        os.execv(sys.executable, [sys.executable, "-m", "app.worker"])
    _run([sys.executable, "-m", "app.bootstrap"])
    port = os.environ.get("PORT", "8000")
    os.execv(
        sys.executable,
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", port],
    )


if __name__ == "__main__":
    main()
