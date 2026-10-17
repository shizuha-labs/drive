from pathlib import Path


def _program_commands():
    commands = {}
    section = None
    for raw_line in Path(__file__).resolve().parents[2].joinpath("supervisord.conf").read_text().splitlines():
        line = raw_line.strip()
        if line.startswith("[program:") and line.endswith("]"):
            section = line[len("[program:") : -1]
            continue
        if section and line.startswith("command="):
            commands[section] = line.split("=", 1)[1]
    return commands


def test_supervisor_uses_uv_venv_entrypoints_for_runtime_processes():
    """The Docker image installs Python deps into /app/.venv via uv sync.

    supervisord does not reliably resolve bare `python`, `gunicorn`, or `celery`
    through the Dockerfile PATH in production, so runtime programs must invoke the
    venv entrypoints directly.
    """

    commands = _program_commands()

    assert commands["mcp-server"].startswith("/app/.venv/bin/python ")
    assert commands["createdb"].startswith("/app/.venv/bin/python ")
    assert commands["django"].startswith("/app/.venv/bin/gunicorn ")
    assert commands["celery-worker"].startswith("/app/.venv/bin/celery ")
    assert commands["celery-beat"].startswith("/app/.venv/bin/celery ")
