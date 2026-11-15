"""Fixed, read-only local NVIDIA diagnostics; no compute or driver installation."""

import csv
import json
import math
import os
import shutil
from pathlib import Path

_QUERY = "--query-gpu=index,name,memory.total,memory.free,utilization.gpu"
_FORMAT = "--format=csv,noheader,nounits"


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _binary():
    """Search host installation locations, never a Worker-controlled directory."""
    if os.name == "nt":
        windows = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        candidates = [windows / "System32" / "nvidia-smi.exe"]
    else:
        candidates = [Path("/usr/bin/nvidia-smi"), Path("/usr/local/nvidia/bin/nvidia-smi")]
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    system_search_path = os.pathsep.join(
        entry for entry in os.defpath.split(os.pathsep) if Path(entry).is_absolute()
    )
    return shutil.which("nvidia-smi", path=system_search_path) if system_search_path else None


def _number(value, *, maximum=None):
    if value.strip().casefold() in {"n/a", "[n/a]", "not supported", "[not supported]"}:
        return None
    result = float(value.strip())
    if not math.isfinite(result) or result < 0 or maximum is not None and result > maximum:
        raise ValueError("Invalid GPU metric")
    return int(result) if result.is_integer() else result


def probe_gpu(*, cwd, env, runner):
    """Call a trusted bounded runner with one fixed hardware inventory command.

    A missing binary or unavailable NVIDIA device returns `unsupported`. The
    diagnostic does not claim support for other vendors and never installs or
    changes drivers. Failed or malformed diagnostic output remains explicit.
    """
    binary = _binary()
    if binary is None:
        return {
            "status": "unsupported",
            "reason": "nvidia_smi_not_installed",
            "devices_json": "[]",
            "probe_json": _json({"executed": False}),
        }
    try:
        process = runner([binary, _QUERY, _FORMAT], cwd, env)
    except (OSError, ValueError):
        return {
            "status": "unavailable",
            "reason": "gpu_probe_failed_or_timed_out",
            "devices_json": "[]",
            "probe_json": _json({"executed": True, "completed": False}),
        }
    encoded = _json(process)
    # Preserve actual output and its digest rather than returning a clipped CSV.
    result = {
        "status": "unsupported",
        "reason": "nvidia_device_or_driver_unavailable",
        "devices_json": "[]",
        "probe_json": encoded,
    }
    if process["exit_code"] != 0:
        return result
    try:
        rows = list(csv.reader(process["stdout"].splitlines(), skipinitialspace=True))
        if len(rows) > 32:
            raise ValueError("Too many devices")
        devices = []
        for row in rows:
            if not row or not any(part.strip() for part in row):
                continue
            if len(row) != 5 or not row[1].strip() or len(row[1]) > 256:
                raise ValueError("Invalid GPU row")
            index = int(row[0].strip())
            if index < 0 or any(device["index"] == index for device in devices):
                raise ValueError("Invalid GPU identity")
            total, free, utilization = (
                _number(row[2]),
                _number(row[3]),
                _number(row[4], maximum=100),
            )
            if total is not None and free is not None and free > total:
                raise ValueError("Invalid GPU memory")
            devices.append(
                {
                    "index": index,
                    "vendor": "nvidia",
                    "name": row[1].strip(),
                    "memory_total_mib": total,
                    "memory_free_mib": free,
                    "utilization_percent": utilization,
                }
            )
    except (ValueError, TypeError, csv.Error):
        result.update(status="unavailable", reason="gpu_probe_output_invalid")
        return result
    if devices:
        result.update(status="available", reason="", devices_json=_json(devices))
    else:
        result["reason"] = "no_nvidia_device"
    return result
