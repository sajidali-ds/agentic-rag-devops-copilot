PLAYBOOK: dict[str, dict] = {
    "CrashLoopBackOff": {
        "meaning": "The container starts, exits, and kubelet restarts it with exponential back-off (10s, 20s, 40s … capped at 5 min).",
        "common_causes": ["Application error at startup (bad config/env vars)", "Missing ConfigMap/Secret value", "Failing liveness probe killing a healthy-but-slow app", "OOMKilled (memory limit too low)", "Wrong command/args or missing binary"],
        "diagnose": ["kubectl logs <pod> -n <ns> --previous", "kubectl describe pod <pod> -n <ns>", "kubectl get events -n <ns> --sort-by=.lastTimestamp"],
    },
    "OOMKilled": {
        "meaning": "The container exceeded its memory limit and was killed by the kernel OOM killer (exit code 137).",
        "common_causes": ["Memory limit lower than real working set", "Memory leak", "JVM/runtime heap sized larger than the container limit", "Burst workloads / large in-memory batches"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>   # Last State: OOMKilled", "kubectl top pod <pod> -n <ns> --containers", "kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.containers[*].resources}'"],
    },
    "ImagePullBackOff": {
        "meaning": "Kubelet cannot pull the image and is backing off (ErrImagePull is the first failure).",
        "common_causes": ["Typo in image name or tag; tag does not exist", "Private registry without imagePullSecrets", "Registry rate limiting (e.g. anonymous Docker Hub pulls)", "Node has no network path / DNS to the registry"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>   # see Events for the exact pull error", "kubectl get pod <pod> -n <ns> -o jsonpath='{.spec.imagePullSecrets}'", "kubectl get secret -n <ns>"],
    },
    "Pending": {
        "meaning": "The pod is accepted but not scheduled to a node (or not yet running).",
        "common_causes": ["Insufficient CPU/memory on every node (compare requests vs node allocatable)", "Taints without matching tolerations", "nodeSelector / affinity matches no node", "PersistentVolumeClaim not bound", "Quota exceeded in the namespace"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>   # Events: FailedScheduling message", "kubectl describe node <node> | grep -A8 'Allocated resources'", "kubectl get pvc -n <ns>", "kubectl get resourcequota -n <ns>"],
    },
    "FailedScheduling": {
        "meaning": "Scheduler found no node that satisfies the pod (message lists why: Insufficient cpu/memory, taints, affinity, PVC).",
        "common_causes": ["Insufficient resources", "Untolerated taints", "Affinity / topology spread constraints", "Unbound PVC or zone mismatch"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>", "kubectl get nodes -o wide", "kubectl describe node <node>"],
    },
    "CreateContainerConfigError": {
        "meaning": "Kubelet could not build the container config, almost always a missing ConfigMap/Secret or key referenced by env/volume.",
        "common_causes": ["ConfigMap or Secret does not exist in the pod's namespace", "Key name typo in configMapKeyRef/secretKeyRef", "Secret created after the pod"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>", "kubectl get configmap,secret -n <ns>"],
    },
    "Evicted": {
        "meaning": "Kubelet evicted the pod because the node was under resource pressure (memory, disk, or PIDs) or the pod exceeded ephemeral-storage limits.",
        "common_causes": ["Node MemoryPressure / DiskPressure", "Pod writing lots of logs/temp files", "No resource requests (BestEffort pods are evicted first)"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>   # Reason/Message", "kubectl describe node <node> | grep -i pressure", "kubectl get events -A --field-selector reason=Evicted"],
    },
    "ProbeFailed": {
        "meaning": "Liveness (restart), readiness (remove from Service), or startup probes are failing.",
        "common_causes": ["Wrong path/port/scheme", "initialDelaySeconds/timeout too small for slow start (use a startupProbe)", "App overloaded or deadlocked", "Probe hits a dependency that is down"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>   # probe config + Events", "kubectl logs <pod> -n <ns>", "kubectl port-forward <pod> 8080:8080   # then curl the probe path"],
    },
    "VolumeMountFailed": {
        "meaning": "A volume could not be attached or mounted (FailedMount / FailedAttachVolume / Multi-Attach error).",
        "common_causes": ["RWO volume still attached to another node", "Missing PVC/Secret/ConfigMap", "CSI driver not healthy", "Zone mismatch between pod and volume"],
        "diagnose": ["kubectl describe pod <pod> -n <ns>", "kubectl get pvc,pv -n <ns>", "kubectl get volumeattachment"],
    },
    "NodeNotReady": {
        "meaning": "The node's kubelet stopped reporting healthy to the control plane.",
        "common_causes": ["kubelet stopped/crashed", "Network partition to API server", "Node under severe resource pressure", "Container runtime down"],
        "diagnose": ["kubectl describe node <node>   # Conditions", "kubectl get nodes", "journalctl -u kubelet --since '15 min ago'   # run on the node"],
    },
    "DNSFailure": {
        "meaning": "Name resolution inside the cluster/pod fails (NXDOMAIN, 'no such host', timeouts to port 53).",
        "common_causes": ["CoreDNS pods unhealthy", "NetworkPolicy blocking UDP/TCP 53", "Wrong search domain / ndots behaviour", "Upstream resolver unreachable"],
        "diagnose": ["kubectl -n kube-system get pods -l k8s-app=kube-dns", "kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50", "kubectl exec -it <pod> -n <ns> -- nslookup kubernetes.default"],
    },
    "ConnectionRefused": {
        "meaning": "TCP connect reached a host but nothing accepted it on that port.",
        "common_causes": ["Service has no ready endpoints (selector mismatch / readiness failing)", "targetPort ≠ container port", "App bound to 127.0.0.1 instead of 0.0.0.0", "App not started yet"],
        "diagnose": ["kubectl get endpoints <svc> -n <ns>", "kubectl describe svc <svc> -n <ns>", "kubectl get pods -n <ns> --show-labels"],
    },
    "NetworkTimeout": {
        "meaning": "Connection attempts get no response (dropped packets) rather than a refusal.",
        "common_causes": ["NetworkPolicy / security group / firewall dropping traffic", "Routing or CNI problem between nodes", "Overloaded or hung backend", "MTU mismatch causing large packets to vanish"],
        "diagnose": ["kubectl get networkpolicy -n <ns>", "kubectl exec -it <pod> -n <ns> -- nc -zv <host> <port>", "ping -M do -s 1400 <node-ip>   # MTU probe, run on a node"],
    },
    "TLSCertificate": {
        "meaning": "TLS verification failed (expired, wrong CA, or hostname/SAN mismatch).",
        "common_causes": ["Expired certificate", "Client does not trust the issuing CA", "Certificate SAN does not include the hostname/IP used"],
        "diagnose": ["openssl s_client -connect <host>:<port> -servername <host> </dev/null | openssl x509 -noout -dates -subject -ext subjectAltName", "kubeadm certs check-expiration   # control-plane certs, run on a control-plane node"],
    },
    "RBACForbidden": {
        "meaning": "The caller's identity lacks permission for the requested verb/resource.",
        "common_causes": ["Missing Role/ClusterRole binding", "Wrong ServiceAccount used by the pod", "Namespace mismatch in the RoleBinding"],
        "diagnose": ["kubectl auth can-i <verb> <resource> -n <ns> --as=system:serviceaccount:<ns>:<sa>", "kubectl get rolebinding,clusterrolebinding -A | grep <sa>"],
    },
    "MTUIssue": {
        "meaning": "Packets larger than the path MTU are dropped or fragmented (common with VXLAN/IPsec overlays).",
        "common_causes": ["Overlay header overhead not subtracted from MTU", "Inconsistent MTU across nodes/NICs", "ICMP 'fragmentation needed' blocked"],
        "diagnose": ["ip link show | grep mtu", "ping -M do -s 1400 <peer-ip>"],
    },
}

EXIT_CODES: dict[int, str] = {
    0: "Success.",
    1: "Generic application error.",
    2: "Misuse of shell builtin / invalid usage.",
    125: "Container runtime failed to run the container.",
    126: "Command found but not executable (permissions).",
    127: "Command not found (wrong entrypoint/binary missing).",
    130: "Terminated by SIGINT (Ctrl-C).",
    137: "SIGKILL (128+9). Most often the OOM killer or a forced kill after SIGTERM grace period.",
    139: "SIGSEGV (128+11): segmentation fault in native code.",
    143: "SIGTERM (128+15): graceful termination requested (normal during rollout/scale-down).",
    255: "Exit status out of range / unspecified runtime error.",
}

_ALIASES = {
    "oom": "OOMKilled", "oomkill": "OOMKilled", "oomkilled": "OOMKilled",
    "crashloop": "CrashLoopBackOff", "crashloopbackoff": "CrashLoopBackOff", "backoff": "CrashLoopBackOff",
    "errimagepull": "ImagePullBackOff", "imagepullbackoff": "ImagePullBackOff", "imagepull": "ImagePullBackOff",
    "pending": "Pending", "unschedulable": "FailedScheduling", "failedscheduling": "FailedScheduling",
    "evicted": "Evicted", "diskpressure": "Evicted", "memorypressure": "Evicted",
    "notready": "NodeNotReady", "nodenotready": "NodeNotReady",
    "dns": "DNSFailure", "nxdomain": "DNSFailure", "dnsfailure": "DNSFailure",
    "connectionrefused": "ConnectionRefused", "refused": "ConnectionRefused",
    "timeout": "NetworkTimeout", "networktimeout": "NetworkTimeout",
    "x509": "TLSCertificate", "tls": "TLSCertificate", "certificate": "TLSCertificate",
    "forbidden": "RBACForbidden", "rbac": "RBACForbidden",
    "probe": "ProbeFailed", "livenessprobe": "ProbeFailed", "readinessprobe": "ProbeFailed", "probefailed": "ProbeFailed",
    "mount": "VolumeMountFailed", "failedmount": "VolumeMountFailed", "volumemountfailed": "VolumeMountFailed",
    "createcontainerconfigerror": "CreateContainerConfigError", "mtu": "MTUIssue", "mtuissue": "MTUIssue",
}


def explain_k8s_status(reason: str) -> dict:
    """Explain a pod status / event reason / exit code with likely causes and first commands."""
    raw = str(reason or "").strip()
    if not raw:
        return {"error": "reason is required", "known": sorted(PLAYBOOK)}

    digits = raw.lower().replace("exit code", "").replace("exitcode", "").strip(" :=")
    if digits.isdigit():
        code = int(digits)
        return {"exit_code": code, "meaning": EXIT_CODES.get(code, f"Exit code {code}: application-defined; check the app's docs/logs."),
                "tip": "Codes above 128 mean 'killed by signal (code-128)'."}

    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    name = _ALIASES.get(key) or next((n for n in PLAYBOOK if n.lower() == key), None)
    if name is None:
        name = next((n for n in PLAYBOOK if n.lower() in key or key in n.lower()), None) if len(key) >= 4 else None
    if name is None:
        return {"error": f"no built-in entry for '{raw}'", "known": sorted(PLAYBOOK)}
    return {"reason": name, **PLAYBOOK[name]}
