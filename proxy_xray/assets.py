import os
import shutil
import subprocess
import time

from .status import log, set_status
from .util import load_json_file, save_json_file


ASSETS = {
    "geoip": {
        "filename": "geoip.dat",
        "url": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat",
        "min_size": 1_000_000,
    },
    "geosite": {
        "filename": "geosite.dat",
        "url": "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat",
        "min_size": 1_000_000,
    },
    "iran": {
        "filename": "iran.dat",
        "url": None,
        "min_size": 1,
    },
}
BUNDLED_ASSET_DIR = "/usr/local/bin"


def asset_state_path(args):
    return os.path.join(args.asset_dir, "assets-state.json")


def asset_file_path(args, item):
    return os.path.join(args.asset_dir, item["filename"])


def file_info(path):
    if not os.path.exists(path):
        return {"status": "missing", "path": path, "size": 0, "mtime": None}
    stat = os.stat(path)
    return {
        "status": "ok",
        "path": path,
        "size": stat.st_size,
        "mtime": stat.st_mtime,
    }


def asset_snapshot(args):
    state = load_json_file(asset_state_path(args), {"assets": {}})
    result = {
        "dir": args.asset_dir,
        "state_file": asset_state_path(args),
        "refresh_interval": args.asset_refresh_interval,
        "last_check_at": state.get("last_check_at"),
        "last_success_at": state.get("last_success_at"),
        "last_status": state.get("last_status"),
        "items": {},
    }
    for name, item in ASSETS.items():
        local = file_info(asset_file_path(args, item))
        saved = state.get("assets", {}).get(name, {})
        result["items"][name] = {
            **local,
            "url": item.get("url"),
            "last_success_at": saved.get("last_success_at"),
            "last_downloaded_size": saved.get("last_downloaded_size"),
            "last_error": saved.get("last_error"),
        }
    return result


def set_asset_status(args):
    set_status(assets=asset_snapshot(args))


def prepare_assets(args):
    os.makedirs(args.asset_dir, exist_ok=True)
    for item in ASSETS.values():
        target = asset_file_path(args, item)
        bundled = os.path.join(BUNDLED_ASSET_DIR, item["filename"])
        if not os.path.exists(target) and os.path.exists(bundled):
            shutil.copy2(bundled, target)
            log(f"seeded asset {item['filename']} from image")
    os.environ["XRAY_LOCATION_ASSET"] = args.asset_dir
    set_asset_status(args)


def download_asset(name, item, args):
    target = asset_file_path(args, item)
    tmp = f"{target}.download"
    command = [
        "curl",
        "-fsSL",
        "--max-time",
        str(args.asset_fetch_timeout),
        "-o",
        tmp,
        item["url"],
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else f"curl exited {result.returncode}"
        raise RuntimeError(detail)
    size = os.path.getsize(tmp)
    if size < item["min_size"]:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError(f"downloaded {name} is too small: {size} bytes")
    os.replace(tmp, target)
    return size


def refresh_assets(args, reason="scheduled"):
    state_path = asset_state_path(args)
    state = load_json_file(state_path, {"assets": {}})
    state.setdefault("assets", {})
    state["last_check_at"] = time.time()
    changed = False
    failures = []

    for name, item in ASSETS.items():
        if not item.get("url"):
            continue
        try:
            before = file_info(asset_file_path(args, item))
            size = download_asset(name, item, args)
            after = file_info(asset_file_path(args, item))
            changed = changed or before.get("size") != after.get("size") or before.get("mtime") != after.get("mtime")
            state["assets"][name] = {
                "last_success_at": time.time(),
                "last_downloaded_size": size,
                "url": item["url"],
                "last_error": None,
            }
            log(f"asset {item['filename']} refreshed from LoyalSoldier ({size} bytes)")
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            saved = state["assets"].setdefault(name, {})
            saved["last_error"] = str(exc)
            log(f"asset {item['filename']} refresh failed: {exc}")

    if failures:
        state["last_status"] = {"status": "warn", "reason": reason, "detail": "; ".join(failures), "time": time.time()}
    else:
        state["last_status"] = {"status": "ok", "reason": reason, "detail": "assets refreshed", "time": time.time()}
        state["last_success_at"] = time.time()
    save_json_file(state_path, state)
    set_asset_status(args)
    return changed
