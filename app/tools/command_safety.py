import re
import shlex

RISK_ORDER = {"read_only": 0, "unknown": 1, "mutating": 2, "destructive": 3}

_READ_ONLY_BINS = {
    "cat", "ls", "grep", "egrep", "tail", "head", "less", "wc", "sort", "uniq", "awk", "cut", "tr", "echo", "date",
    "journalctl", "dmesg", "ps", "top", "htop", "df", "du", "free", "uptime", "uname", "hostname", "whoami", "id",
    "ss", "netstat", "ping", "traceroute", "tracepath", "mtr", "dig", "nslookup", "host", "nc", "ncat", "telnet",
    "lscpu", "lspci", "lsblk", "lsmod", "numactl", "numastat", "iostat", "vmstat", "sar", "tcpdump", "jq", "yq",
    "stat", "file", "find", "env", "printenv", "which", "lsof", "ip",
}
_K8S_READ = {"get", "describe", "logs", "top", "explain", "api-resources", "api-versions", "version", "cluster-info",
             "events", "diff", "port-forward", "wait", "auth"}
_K8S_MUT = {"apply", "create", "patch", "replace", "edit", "scale", "annotate", "label", "set", "expose", "autoscale",
            "run", "taint", "cordon", "uncordon", "exec", "cp", "attach", "debug", "certificate"}
_K8S_DESTRUCT = {"delete", "drain"}
_HELM_READ = {"list", "ls", "status", "get", "history", "template", "lint", "show", "search", "repo", "version"}
_HELM_MUT = {"install", "upgrade", "rollback", "pull", "dependency"}
_HELM_DESTRUCT = {"uninstall", "delete"}
_DOCKER_READ = {"ps", "logs", "inspect", "images", "stats", "info", "version", "top", "history", "ls"}
_DOCKER_DESTRUCT = {"rm", "rmi", "kill", "prune"}
_SYSTEMCTL_READ = {"status", "is-active", "is-enabled", "is-failed", "list-units", "list-unit-files", "cat", "show"}

_ALWAYS_DESTRUCTIVE = [
    (re.compile(r"\brm\s+(-[a-zA-Z]*[rf][a-zA-Z]*\s+)+"), "recursive/forced file removal"),
    (re.compile(r"\brm\b"), "removes files"),
    (re.compile(r"\b(mkfs(\.\w+)?|fdisk|parted|wipefs|shred)\b"), "disk/filesystem destructive operation"),
    (re.compile(r"\bdd\b.*\bof=/dev/"), "writes directly to a block device"),
    (re.compile(r"\b(shutdown|reboot|poweroff|halt)\b"), "powers off / restarts the machine"),
    (re.compile(r"\bkubeadm\s+reset\b"), "wipes the node's Kubernetes state"),
    (re.compile(r"\|\s*(sudo\s+)?(ba|z)?sh\b"), "pipes remote/unknown content into a shell"),
    (re.compile(r"chmod\s+(-R\s+)?777"), "world-writable permissions"),
    (re.compile(r"\biptables\b.*\s-F\b|\bnft\s+flush\b"), "flushes firewall rules (can cut connectivity)"),
    (re.compile(r">\s*/dev/(sd|nvme|vd)"), "overwrites a block device"),
]


def _segments(command: str) -> list[str]:
    # split on shell control operators; pipes keep the right-hand side as its own command
    return [s.strip() for s in re.split(r"\|\||&&|;|\|", command) if s.strip()]


def _classify_segment(seg: str) -> tuple[str, list[str]]:
    try:
        toks = shlex.split(seg)
    except ValueError:
        return "unknown", ["could not parse command"]
    while toks and toks[0] in {"sudo", "env", "time", "nohup"}:
        toks = toks[1:]
    if not toks:
        return "unknown", ["empty command"]
    binary, args = toks[0].split("/")[-1], toks[1:]
    verbs = [a for a in args if not a.startswith("-")]
    verb = verbs[0] if verbs else ""
    flags = set(a for a in args if a.startswith("-"))

    if binary == "kubectl" or binary == "oc":
        if verb in _K8S_DESTRUCT:
            return "destructive", [f"kubectl {verb} removes/evicts workloads"]
        if verb == "rollout":
            sub = verbs[1] if len(verbs) > 1 else ""
            return ("read_only", []) if sub in {"status", "history"} else ("mutating", [f"kubectl rollout {sub} changes workloads"])
        if verb == "config":
            sub = verbs[1] if len(verbs) > 1 else ""
            return ("read_only", []) if sub in {"view", "get-contexts", "current-context"} else ("mutating", ["changes kubeconfig"])
        if verb == "auth":
            return "read_only", []
        if verb in _K8S_MUT:
            r = ["kubectl %s modifies cluster state" % verb]
            if verb == "exec":
                r = ["exec runs arbitrary commands inside a container"]
            return "mutating", r
        if verb in _K8S_READ:
            return "read_only", []
        return "unknown", [f"unrecognised kubectl verb '{verb}'"]
    if binary == "kubeadm":
        if verb == "certs" and "check-expiration" in args:
            return "read_only", []
        return "mutating", ["kubeadm changes node/cluster state"]
    if binary == "helm":
        if verb in _HELM_DESTRUCT:
            return "destructive", ["helm uninstall removes a release"]
        if verb in _HELM_MUT:
            return "mutating", [f"helm {verb} changes the release"]
        if verb in _HELM_READ:
            return "read_only", []
        return "unknown", [f"unrecognised helm verb '{verb}'"]
    if binary in {"docker", "podman", "crictl", "nerdctl"}:
        if verb in _DOCKER_DESTRUCT or (verb == "system" and "prune" in args) or (verb == "volume" and "rm" in args):
            return "destructive", [f"{binary} {verb} deletes containers/images/volumes"]
        if verb in _DOCKER_READ or verb in {"pods", "images", "ps"}:
            return "read_only", []
        return "mutating", [f"{binary} {verb} changes container state"]
    if binary == "systemctl":
        return ("read_only", []) if verb in _SYSTEMCTL_READ else ("mutating", [f"systemctl {verb} changes a service"])
    if binary == "curl":
        if flags & {"-X", "--request"} and any(a.upper() in {"POST", "PUT", "PATCH", "DELETE"} for a in args):
            return "mutating", ["sends a state-changing HTTP request"]
        if flags & {"-d", "--data", "--data-raw", "-F", "-T", "--upload-file"}:
            return "mutating", ["sends a request body"]
        return "read_only", []
    if binary == "iptables" or binary == "ip6tables":
        return ("read_only", []) if flags & {"-L", "-S", "-n", "-v"} and not flags & {"-A", "-I", "-D", "-X", "-P"} else ("mutating", ["changes firewall rules"])
    if binary == "sysctl":
        return ("mutating", ["changes a kernel parameter"]) if ("-w" in flags or any("=" in a for a in args)) else ("read_only", [])
    if binary == "ethtool":
        return ("mutating", ["changes NIC settings"]) if any(f.startswith("-") and f not in {"-S", "-i", "-k", "-g", "-c", "-a", "-P", "-T"} for f in flags) else ("read_only", [])
    if binary == "ip":
        return ("read_only", []) if verb in {"a", "addr", "address", "r", "route", "l", "link", "n", "neigh", "neighbor", "rule", "netns", "-s"} and not {"add", "del", "delete", "set", "flush"} & set(args) else ("mutating", ["changes network configuration"])
    if binary == "openssl":
        return "read_only", []
    if binary in _READ_ONLY_BINS:
        return "read_only", []
    return "unknown", [f"'{binary}' is not in the known-command list; review manually"]


def check_command(command: str) -> dict:
    """Classify a (possibly piped) shell command as read_only / mutating / destructive / unknown."""
    cmd = (command or "").strip()
    if not cmd:
        return {"error": "command is required"}

    risk, reasons = "read_only", []

    def bump(r: str, why: list[str]) -> None:
        nonlocal risk
        if RISK_ORDER[r] > RISK_ORDER[risk]:
            risk = r
        reasons.extend(w for w in why if w not in reasons)

    for rx, why in _ALWAYS_DESTRUCTIVE:
        if rx.search(cmd):
            bump("destructive", [why])
    for seg in _segments(cmd):
        r, why = _classify_segment(seg)
        bump(r, why)

    if re.search(r"\bdelete\b.*(--all\b|--force\b|\bnamespaces?\b|\bns\b)", cmd):
        bump("destructive", ["bulk / forced / namespace-level deletion"])
    out = {"command": cmd, "risk": risk, "reasons": reasons}
    if risk in {"mutating", "destructive"}:
        out["advice"] = "Prefer a read-only check first (get/describe/logs); run in a test namespace or with --dry-run=server where supported."
    return out


_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]{4,200})`")
_KNOWN_START = ("kubectl", "helm", "docker", "podman", "crictl", "systemctl", "journalctl", "ip ", "ss ", "iptables", "sysctl",
                "ethtool", "curl", "kubeadm", "numactl", "ping", "dig", "nslookup", "nc ", "openssl", "sudo", "rm ", "oc ")


def extract_commands(text: str) -> list[str]:
    """Pull shell commands out of fenced code blocks and `inline code` in an answer."""
    cmds: list[str] = []
    for block in _FENCE.findall(text or ""):
        for line in block.splitlines():
            line = line.strip().lstrip("$ ").strip()
            if line and not line.startswith("#") and line.split()[0] not in {"apiVersion:", "kind:"} and not line.endswith(":"):
                cmds.append(line)
    for m in _INLINE.findall(re.sub(_FENCE, "", text or "")):
        if m.strip().startswith(_KNOWN_START):
            cmds.append(m.strip())
    seen, out = set(), []
    for c in cmds:
        c = c.split("  #")[0].strip()  # drop trailing comments like "  # run on the node"
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def review_commands(answer: str) -> list[dict]:
    return [check_command(c) for c in extract_commands(answer)]


def safety_notice(reviews: list[dict]) -> str:
    """Markdown notice listing every command that is not read-only. Empty if all safe."""
    risky = [r for r in reviews if r.get("risk") in {"mutating", "destructive"}]
    if not risky:
        return ""
    lines = ["", "", "> ⚠️ **Command safety review.** I never run commands — these suggestions change state, so review them first:"]
    for r in risky:
        label = "DESTRUCTIVE" if r["risk"] == "destructive" else "changes state"
        lines.append(f"> - `{r['command']}` — **{label}** ({'; '.join(r['reasons'])})")
    return "\n".join(lines)
