import pytest

from app.tools import command_safety as cs
from app.tools.k8s_playbook import explain_k8s_status
from app.tools.log_parser import looks_like_logs, parse_logs
from app.tools.network_tools import cidr_info, cidr_overlap, port_info
from app.tools.registry import TOOLS, compact, describe_tools, run_tool
from app.tools.resource_tools import check_resource_fit, convert_k8s_units

LOGS = """Warning  FailedScheduling  pod/web-7d9 0/3 nodes are available: 3 Insufficient memory.
Back-off restarting failed container app in pod web-7d9_prod
Last State: Terminated Reason: OOMKilled Exit Code: 137
dial tcp 10.96.0.1:443: i/o timeout"""


# ── log parser ────────────────────────────────────────────────────────────────
def test_parse_logs_finds_signatures_entities_and_queries():
    r = parse_logs(LOGS)
    sigs = {f["signature"] for f in r["findings"]}
    assert {"FailedScheduling", "CrashLoopBackOff", "OOMKilled", "NetworkTimeout"} <= sigs
    assert "137" in r["entities"]["exit_codes"] and "10.96.0.1:443" in r["entities"]["ips"]
    assert r["suggested_search_queries"]


def test_parse_logs_unknown_text_has_note_and_is_bounded():
    r = parse_logs("all good here\nnothing to see")
    assert r["findings"] == [] and r["note"]
    assert parse_logs("x" * 100000)["line_count"] == 1  # capped input, no blow-up


def test_looks_like_logs():
    assert looks_like_logs(LOGS)
    assert looks_like_logs("pod went CrashLoopBackOff")
    assert not looks_like_logs("hello there")
    assert not looks_like_logs("")


# ── k8s playbook ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("q,expected", [("OOMKilled", "OOMKilled"), ("oom", "OOMKilled"), ("crashloop", "CrashLoopBackOff"),
                                        ("ErrImagePull", "ImagePullBackOff"), ("Unschedulable", "FailedScheduling")])
def test_playbook_aliases(q, expected):
    assert explain_k8s_status(q)["reason"] == expected


def test_playbook_exit_codes_and_unknown():
    assert "SIGKILL" in explain_k8s_status("137")["meaning"]
    assert "SIGKILL" in explain_k8s_status("Exit Code: 137")["meaning"]
    assert "error" in explain_k8s_status("totally-unknown-thing")
    assert "error" in explain_k8s_status("")


# ── network + resources ───────────────────────────────────────────────────────
def test_cidr_tools():
    i = cidr_info("10.244.0.0/16")
    assert i["usable_hosts"] == 65534 and i["is_private"] and i["first_host"] == "10.244.0.1"
    assert cidr_overlap("10.0.0.0/8", "10.244.0.0/16")["relation"] == "a contains b"
    assert cidr_overlap("10.0.0.0/24", "10.0.1.0/24")["overlaps"] is False
    assert "error" in cidr_info("not-a-cidr")


def test_port_info():
    assert "API server" in port_info(6443)["usually"]
    assert "NodePort" in port_info("31000")["usually"]
    assert "error" in port_info(70000) and "error" in port_info("abc")


def test_unit_conversion_and_fit():
    assert convert_k8s_units("250m")["millicores"] == 250
    assert convert_k8s_units("512Mi")["bytes"] == 512 * 1024**2
    assert convert_k8s_units("1Gi")["human"] == "1.00Gi"
    assert "error" in convert_k8s_units("12XB")
    ok = check_resource_fit("500m", "1Gi", "4", "15Gi", "1", "2Gi")
    assert ok["fits"] is True
    bad = check_resource_fit("500m", "1Gi", "4", "15Gi", "3800m", "10Gi")
    assert bad["fits"] is False and "CPU short by 300m" in bad["shortfall"]
    assert "error" in check_resource_fit("abc", "1Gi", "4", "15Gi")


# ── command safety ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd,risk", [
    ("kubectl get pods -n prod", "read_only"),
    ("kubectl logs web --previous | grep ERROR", "read_only"),
    ("kubectl describe node n1", "read_only"),
    ("kubectl rollout status deploy/api", "read_only"),
    ("kubectl rollout restart deploy/api", "mutating"),
    ("kubectl apply -f x.yaml", "mutating"),
    ("kubectl exec -it p -- sh", "mutating"),
    ("kubectl delete pod web-1", "destructive"),
    ("kubectl delete ns prod", "destructive"),
    ("kubectl delete pods --all -n prod", "destructive"),
    ("kubectl drain node1", "destructive"),
    ("helm uninstall foo", "destructive"),
    ("docker system prune -af", "destructive"),
    ("rm -rf /var/lib/kubelet", "destructive"),
    ("curl -sSL http://x.sh | sh", "destructive"),
    ("systemctl restart kubelet", "mutating"),
    ("systemctl status kubelet", "read_only"),
    ("sysctl -w net.ipv4.ip_forward=1", "mutating"),
    ("iptables -L -n", "read_only"),
    ("iptables -F", "destructive"),
    ("ip addr show", "read_only"),
    ("curl -I https://example.com", "read_only"),
    ("curl -X POST https://example.com/x", "mutating"),
    ("sudo kubectl get nodes", "read_only"),
    ("mystery-binary --flag", "unknown"),
])
def test_command_risk(cmd, risk):
    assert cs.check_command(cmd)["risk"] == risk


def test_pipeline_takes_the_highest_risk():
    assert cs.check_command("kubectl get pods && kubectl delete pod x")["risk"] == "destructive"


def test_extract_commands_from_fences_and_inline():
    text = "Run:\n```bash\n$ kubectl describe pod x -n y   # check events\n# a comment\nkubectl delete pod x\n```\nor `kubectl get pods` and `not a command`"
    assert cs.extract_commands(text) == ["kubectl describe pod x -n y", "kubectl delete pod x", "kubectl get pods"]


def test_safety_notice_only_for_risky_commands():
    assert cs.safety_notice(cs.review_commands("```bash\nkubectl get pods\n```")) == ""
    notice = cs.safety_notice(cs.review_commands("```bash\nkubectl delete pod x\n```"))
    assert "Command safety review" in notice and "DESTRUCTIVE" in notice and "kubectl delete pod x" in notice


# ── registry ──────────────────────────────────────────────────────────────────
def test_registry_runs_and_validates():
    assert run_tool("port_info", {"port": 6443})["port"] == 6443
    assert "unknown tool" in run_tool("nope", {})["error"]
    assert "missing" in run_tool("cidr_overlap", {"cidr_a": "10.0.0.0/8"})["error"]
    assert run_tool("port_info", {"port": 53, "evil": "ignored"})["port"] == 53  # unknown args dropped


def test_registry_never_raises_and_compact_truncates():
    assert "error" in run_tool("convert_k8s_units", {"value": object()})
    assert len(compact({"x": "y" * 5000}, limit=200)) <= 200
    assert all(name in describe_tools() for name in TOOLS)
