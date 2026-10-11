import subprocess
import time

XRAY = "/usr/local/bin/xray"


def terminate_process(proc):
    if not proc or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def curl_check(port, url, timeout):
    command = [
        "curl",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(timeout),
        "-w",
        "%{time_total}",
        "-x",
        f"socks5h://127.0.0.1:{port}",
        url,
    ]
    started = time.monotonic()
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return False, float(timeout)
    elapsed = time.monotonic() - started
    if result.returncode != 0:
        return False, elapsed
    try:
        elapsed = float(result.stdout.strip())
    except ValueError:
        pass
    return True, elapsed


def throughput_check(port, url, timeout):
    command = [
        "curl",
        "-f",
        "-o",
        "/dev/null",
        "-sS",
        "--max-time",
        str(timeout),
        "-w",
        "%{speed_download}",
        "-x",
        f"socks5h://127.0.0.1:{port}",
        url,
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout + 2)
    except subprocess.TimeoutExpired:
        return False, 0.0
    if result.returncode != 0:
        return False, 0.0
    try:
        bytes_per_second = float(result.stdout.strip())
    except ValueError:
        return False, 0.0
    return True, bytes_per_second * 8 / 1000
