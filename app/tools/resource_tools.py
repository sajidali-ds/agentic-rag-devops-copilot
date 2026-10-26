import re

_MEM = {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5, "Ei": 1024**6,
        "k": 1000, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5, "E": 1000**6}
_QTY = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]*)\s*$")


def _parse_cpu(value) -> float:
    m = _QTY.match(str(value))
    if not m:
        raise ValueError(f"invalid CPU quantity: {value!r}")
    num, unit = float(m.group(1)), m.group(2)
    if unit == "m":
        return num
    if unit == "":
        return num * 1000
    raise ValueError(f"invalid CPU unit {unit!r} (use cores like '2' or millicores like '250m')")


def _parse_mem(value) -> float:
    m = _QTY.match(str(value))
    if not m:
        raise ValueError(f"invalid memory quantity: {value!r}")
    num, unit = float(m.group(1)), m.group(2)
    if unit == "":
        return num
    if unit in _MEM:
        return num * _MEM[unit]
    raise ValueError(f"invalid memory unit {unit!r} (use Ki/Mi/Gi/... or k/M/G/...)")


def _human_mem(b: float) -> str:
    for unit, size in (("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024)):
        if abs(b) >= size:
            return f"{b / size:.2f}{unit}"
    return f"{b:.0f}B"


def convert_k8s_units(value: str) -> dict:
    """Normalise a Kubernetes quantity: '250m'/'1.5' (CPU) or '512Mi'/'2Gi'/'500M' (memory)."""
    s = str(value).strip()
    if re.fullmatch(r"\d*\.?\d+m", s):
        try:
            return {"input": s, "type": "cpu", "millicores": _parse_cpu(s), "cores": _parse_cpu(s) / 1000}
        except ValueError:
            pass
    try:
        if re.fullmatch(r"\d*\.?\d+", s):
            cores = float(s)
            return {"input": s, "type": "cpu_or_bytes", "as_cpu_millicores": cores * 1000, "as_bytes": cores,
                    "note": "bare number: cores if this is a CPU value, bytes if memory"}
        b = _parse_mem(s)
        return {"input": s, "type": "memory", "bytes": b, "human": _human_mem(b)}
    except ValueError as e:
        return {"error": str(e)}


def check_resource_fit(request_cpu: str, request_memory: str, node_cpu: str, node_memory: str,
                       used_cpu: str = "0", used_memory: str = "0") -> dict:
    """Would a pod's *requests* fit on a node? Use node ALLOCATABLE (not capacity) and the sum of existing requests as 'used'."""
    try:
        free_cpu = _parse_cpu(node_cpu) - _parse_cpu(used_cpu)
        free_mem = _parse_mem(node_memory) - _parse_mem(used_memory)
        need_cpu, need_mem = _parse_cpu(request_cpu), _parse_mem(request_memory)
    except ValueError as e:
        return {"error": str(e)}
    fits_cpu, fits_mem = need_cpu <= free_cpu, need_mem <= free_mem
    out = {
        "fits": fits_cpu and fits_mem,
        "cpu": {"requested_m": need_cpu, "free_m": free_cpu, "fits": fits_cpu},
        "memory": {"requested": _human_mem(need_mem), "free": _human_mem(free_mem), "fits": fits_mem},
    }
    short = []
    if not fits_cpu:
        short.append(f"CPU short by {need_cpu - free_cpu:.0f}m")
    if not fits_mem:
        short.append(f"memory short by {_human_mem(need_mem - free_mem)}")
    if short:
        out["shortfall"] = ", ".join(short)
    return out
