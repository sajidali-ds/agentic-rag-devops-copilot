"""Deterministic log / error triage. No LLM: fast, free, and testable."""
import re
from collections import Counter

MAX_CHARS = 20000

# (regex, signature, category, hint-for-the-agent)
SIGNATURES: list[tuple[str, str, str, str]] = [
    (r"CrashLoopBackOff|Back-?off restarting failed container", "CrashLoopBackOff", "kubernetes", "Container keeps crashing; read previous logs and exit code."),
    (r"OOMKilled|Out of memory: Kill|oom-kill|Exit Code:\s*137", "OOMKilled", "kubernetes", "Memory limit exceeded (exit 137). Compare usage vs limit."),
    (r"ImagePullBackOff|ErrImagePull|Back-?off pulling image|manifest unknown|pull access denied", "ImagePullBackOff", "kubernetes", "Image cannot be pulled: name/tag, registry auth or rate limit."),
    (r"CreateContainerConfigError|configmap \"[^\"]+\" not found|secret \"[^\"]+\" not found", "CreateContainerConfigError", "kubernetes", "Referenced ConfigMap/Secret/key is missing."),
    (r"FailedScheduling|0/\d+ nodes are available|Insufficient (?:cpu|memory)|had untolerated taint|didn't match Pod's node affinity", "FailedScheduling", "kubernetes", "Pod cannot be scheduled: resources, taints, affinity or PVC."),
    (r"Evicted|The node was low on resource|DiskPressure|MemoryPressure|PIDPressure", "Evicted", "kubernetes", "Node pressure eviction."),
    (r"Liveness probe failed|Readiness probe failed|Startup probe failed", "ProbeFailed", "kubernetes", "Health probe failing: path/port/timing."),
    (r"FailedMount|FailedAttachVolume|Multi-Attach error|Unable to attach or mount volumes", "VolumeMountFailed", "kubernetes", "Volume attach/mount problem."),
    (r"NodeNotReady|node .* not ready|KubeletNotReady", "NodeNotReady", "kubernetes", "Node unhealthy: kubelet, network or pressure."),
    (r"forbidden: User|is forbidden|Unauthorized|RBAC", "RBACForbidden", "security", "Missing RBAC permission."),
    (r"x509: certificate|certificate has expired|certificate signed by unknown authority|tls: failed to verify", "TLSCertificate", "security", "Certificate expired/untrusted/SAN mismatch."),
    (r"no such host|NXDOMAIN|Temporary failure in name resolution|server misbehaving|could not resolve host", "DNSFailure", "network", "DNS resolution failing (CoreDNS, ndots, NetworkPolicy)."),
    (r"connection refused|ECONNREFUSED", "ConnectionRefused", "network", "Nothing listening / no endpoints / wrong port."),
    (r"i/o timeout|ETIMEDOUT|connection timed out|context deadline exceeded|Client\.Timeout exceeded", "NetworkTimeout", "network", "Packets dropped: firewall, NetworkPolicy, routing or overloaded backend."),
    (r"no route to host|Network is unreachable|ENETUNREACH", "NoRoute", "network", "Routing/CNI problem."),
    (r"connection reset by peer|ECONNRESET|broken pipe", "ConnectionReset", "network", "Peer closed the connection (crash, idle timeout, LB)."),
    (r"Segmentation fault|SIGSEGV|Exit Code:\s*139|core dumped", "Segfault", "application", "Native crash (exit 139)."),
    (r"Traceback \(most recent call last\)|panic:|Unhandled exception|java\.lang\.\w+(?:Exception|Error)|NullPointerException", "ApplicationException", "application", "Unhandled application exception; read the top of the stack trace."),
    (r"permission denied|EACCES|Operation not permitted", "PermissionDenied", "system", "File/syscall permission or securityContext issue."),
    (r"No space left on device|ENOSPC|disk quota exceeded", "DiskFull", "system", "Disk or inode exhaustion."),
    (r"too many open files|EMFILE", "FdLimit", "system", "File-descriptor limit reached (ulimit)."),
    (r"MTU|fragmentation needed|packet too big", "MTUIssue", "network", "MTU mismatch / fragmentation."),
    (r"NUMA|numa_|cross-node|remote memory", "NUMAHint", "performance", "NUMA locality may matter."),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), s, c, h) for p, s, c, h in SIGNATURES]

_SEVERITY = {
    "FATAL": re.compile(r"\bFATAL\b|\bPANIC\b|\bpanic:", re.IGNORECASE),
    "ERROR": re.compile(r"\bERROR\b|\bERR\b|\blevel=error\b|\bfailed\b", re.IGNORECASE),
    "WARN": re.compile(r"\bWARN(?:ING)?\b|\blevel=warn", re.IGNORECASE),
}
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?\b")
_POD = re.compile(r"\bpods?[/ ]\"?([a-z0-9][a-z0-9.-]{2,62})", re.IGNORECASE)
_NS = re.compile(r"(?:\bnamespace[: =]+\"?|\s-n\s+)([a-z0-9][a-z0-9-]{0,62})", re.IGNORECASE)
_EXIT = re.compile(r"[Ee]xit(?: [Cc]ode| status)?[: =]+(\d{1,3})")
_HTTP = re.compile(r"\b(?:HTTP/\d(?:\.\d)?\"?\s+|status[= :]+|code[= :]+)([1-5]\d\d)\b")

_QUERY_HINTS = {
    "CrashLoopBackOff": "kubernetes CrashLoopBackOff troubleshooting",
    "OOMKilled": "container OOMKilled memory limit exit code 137",
    "ImagePullBackOff": "ImagePullBackOff image pull error registry secret",
    "CreateContainerConfigError": "CreateContainerConfigError missing configmap secret",
    "FailedScheduling": "pod pending FailedScheduling insufficient resources taints",
    "Evicted": "pod evicted node pressure",
    "ProbeFailed": "liveness readiness probe failed troubleshooting",
    "VolumeMountFailed": "FailedMount persistent volume attach troubleshooting",
    "NodeNotReady": "node NotReady kubelet troubleshooting",
    "DNSFailure": "kubernetes DNS resolution CoreDNS troubleshooting",
    "ConnectionRefused": "service connection refused no endpoints",
    "NetworkTimeout": "network timeout NetworkPolicy firewall troubleshooting",
    "TLSCertificate": "x509 certificate expired kubernetes",
    "MTUIssue": "MTU mismatch overlay network fragmentation",
}


def looks_like_logs(text: str) -> bool:
    """Heuristic used by the planner to decide whether to run :func:`parse_logs` automatically."""
    if not text:
        return False
    lines = text.strip().splitlines()
    if len(lines) >= 4:
        return True
    return any(rx.search(text) for rx, *_ in _COMPILED[:16])


def _uniq(items, limit=10):
    seen, out = set(), []
    for i in items:
        if i not in seen:
            seen.add(i)
            out.append(i)
        if len(out) >= limit:
            break
    return out


def parse_logs(text: str) -> dict:
    """Extract known failure signatures, entities and suggested search queries from raw logs/events."""
    text = (text or "")[:MAX_CHARS]
    lines = [ln for ln in text.splitlines() if ln.strip()]

    findings: list[dict] = []
    seen: set[str] = set()
    for line in lines:
        for rx, sig, cat, hint in _COMPILED:
            if sig not in seen and rx.search(line):
                seen.add(sig)
                findings.append({"signature": sig, "category": cat, "evidence": line.strip()[:200], "hint": hint})
    # signatures can also span lines (e.g. "Exit Code:" on its own line); fall back to full text
    for rx, sig, cat, hint in _COMPILED:
        if sig not in seen and (m := rx.search(text)):
            seen.add(sig)
            findings.append({"signature": sig, "category": cat, "evidence": m.group(0)[:200], "hint": hint})

    severity = Counter()
    for line in lines:
        for name, rx in _SEVERITY.items():
            if rx.search(line):
                severity[name] += 1
                break

    entities = {
        "pods": _uniq(_POD.findall(text)),
        "namespaces": _uniq(_NS.findall(text)),
        "ips": _uniq(_IP.findall(text)),
        "exit_codes": _uniq(_EXIT.findall(text)),
        "http_status": _uniq(_HTTP.findall(text)),
    }
    queries = _uniq([_QUERY_HINTS[f["signature"]] for f in findings if f["signature"] in _QUERY_HINTS], 4)

    return {
        "line_count": len(lines),
        "severity_counts": dict(severity),
        "findings": findings[:8],
        "entities": {k: v for k, v in entities.items() if v},
        "suggested_search_queries": queries,
        "note": "no known failure signature matched" if not findings else None,
    }
