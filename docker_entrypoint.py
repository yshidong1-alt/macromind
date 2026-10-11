"""Prepare a newly mounted disk, then run the server as an unprivileged user."""
import os
from pathlib import Path

path = Path(os.environ.get("DB_PATH", "/var/data/macromind.sqlite3"))
path.parent.mkdir(parents=True, exist_ok=True)
if os.getuid() == 0:
    # Only touch this application's database and its SQLite sidecar files.
    for item in [path.parent, path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]:
        if item.exists():
            os.chown(item, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execvp("gunicorn", ["gunicorn", "-c", "gunicorn.conf.py", "wsgi:app"])
