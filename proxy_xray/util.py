import json
import os
import tempfile


def qfirst(query, *names, default=""):
    for name in names:
        values = query.get(name)
        if values:
            return values[0]
    return default


def csv(value):
    return [item.strip() for item in value.split(",") if item.strip()]


def maybe_bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def load_json_file(path, default):
    if not path:
        return default
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def save_json_file(path, data):
    if not path:
        return
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".atomic-", dir=directory or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(directory or ".", os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def shallow_merge(base, overlay):
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            nested = dict(result[key])
            nested.update(value)
            result[key] = nested
        else:
            result[key] = value
    return result
