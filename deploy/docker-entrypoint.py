"""Make host bind mounts writable, then launch the application as a non-root user."""

import os
import sys
from pathlib import Path

if os.getuid() == 0:
    for variable in ("TREND_SIFT_DB_PATH", "TREND_SIFT_LOG_DIR"):
        path = Path(os.environ[variable])
        directory = path.parent if variable == "TREND_SIFT_DB_PATH" else path
        directory.mkdir(parents=True, exist_ok=True)
        os.chown(directory, 10001, 10001)
        for child in directory.iterdir():
            if child.is_file() and not child.is_symlink():
                os.chown(child, 10001, 10001)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)

os.execvp(sys.argv[1], sys.argv[1:])
