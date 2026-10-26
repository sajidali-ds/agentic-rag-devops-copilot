"""Tool registry: name, description, parameters and a safe runner for the agent."""
import json
from dataclasses import dataclass, field
from typing import Callable

from app.config import settings
from app.observability.stats import stats
from app.tools.command_safety import check_command
from app.tools.k8s_playbook import explain_k8s_status
from app.tools.log_parser import parse_logs
from app.tools.network_tools import cidr_info, cidr_overlap, port_info
from app.tools.resource_tools import check_resource_fit, convert_k8s_units


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, str]  # param -> description
    fn: Callable
    required: list[str] = field(default_factory=list)


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in [
        Tool("parse_logs", "Triage raw logs / kubectl output: detects known failure signatures (CrashLoopBackOff, OOMKilled, DNS, TLS, …), entities and suggested searches.",
             {"text": "the raw log / events text"}, parse_logs, ["text"]),
        Tool("explain_k8s_status", "Explain a Kubernetes pod status, event reason, or exit code (e.g. 'OOMKilled', 'ImagePullBackOff', '137') with likely causes and first diagnostic commands.",
             {"reason": "status/reason/exit code"}, explain_k8s_status, ["reason"]),
        Tool("check_resource_fit", "Check whether a pod's CPU/memory REQUESTS fit on a node's allocatable resources.",
             {"request_cpu": "e.g. 500m", "request_memory": "e.g. 1Gi", "node_cpu": "node allocatable CPU, e.g. 4", "node_memory": "node allocatable memory, e.g. 15Gi",
              "used_cpu": "sum of existing requests (optional)", "used_memory": "sum of existing requests (optional)"},
             check_resource_fit, ["request_cpu", "request_memory", "node_cpu", "node_memory"]),
        Tool("convert_k8s_units", "Convert a Kubernetes CPU/memory quantity (250m, 1.5, 512Mi, 2Gi, 500M) to millicores / bytes.",
             {"value": "the quantity"}, convert_k8s_units, ["value"]),
        Tool("cidr_info", "Subnet maths for a CIDR (network, netmask, usable hosts, private?).",
             {"cidr": "e.g. 10.244.0.0/16"}, cidr_info, ["cidr"]),
        Tool("cidr_overlap", "Check whether two CIDRs overlap (pod/service/node/VPN ranges).",
             {"cidr_a": "first CIDR", "cidr_b": "second CIDR"}, cidr_overlap, ["cidr_a", "cidr_b"]),
        Tool("port_info", "What usually listens on a port (Kubernetes components, NodePort range, common services).",
             {"port": "port number"}, port_info, ["port"]),
        Tool("check_command", "Safety-classify a shell command you intend to suggest (read_only / mutating / destructive).",
             {"command": "the full command"}, check_command, ["command"]),
    ]
}


def describe_tools() -> str:
    lines = []
    for t in TOOLS.values():
        params = ", ".join(f"{k} ({v})" for k, v in t.params.items())
        lines.append(f"- {t.name}: {t.description} Args: {params}")
    return "\n".join(lines)


def run_tool(name: str, args: dict | None) -> dict:
    """Run a tool defensively. Always returns a dict; never raises."""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"unknown tool '{name}'", "available": sorted(TOOLS)}
    args = {k: v for k, v in (args or {}).items() if k in tool.params}
    missing = [p for p in tool.required if p not in args or args[p] in (None, "")]
    if missing:
        return {"error": f"missing required argument(s): {', '.join(missing)}", "params": tool.params}
    stats.tool(name)
    try:
        return tool.fn(**args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def compact(result: dict, limit: int | None = None) -> str:
    """JSON string capped to the observation budget so the agent prompt stays small."""
    limit = limit or settings.AGENT_OBSERVATION_CHARS
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 15] + " …[truncated]"
