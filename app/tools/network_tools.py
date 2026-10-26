import ipaddress

WELL_KNOWN_PORTS: dict[int, str] = {
    22: "SSH", 53: "DNS (UDP/TCP)", 80: "HTTP", 123: "NTP (UDP)", 179: "BGP (Calico/MetalLB)",
    443: "HTTPS", 2379: "etcd client", 2380: "etcd peer", 3306: "MySQL", 4789: "VXLAN (UDP)",
    5000: "Container registry (conventional)", 5432: "PostgreSQL", 6379: "Redis",
    6443: "Kubernetes API server", 8080: "HTTP alternative / common app port", 8443: "HTTPS alternative",
    8472: "Flannel VXLAN (UDP)", 9090: "Prometheus", 9100: "node-exporter", 9200: "Elasticsearch",
    10250: "kubelet API", 10256: "kube-proxy health", 10257: "kube-controller-manager (secure)",
    10259: "kube-scheduler (secure)",
}


def cidr_info(cidr: str) -> dict:
    """Subnet maths for an IPv4/IPv6 network or interface address, e.g. 10.244.0.0/16."""
    try:
        net = ipaddress.ip_network(str(cidr).strip(), strict=False)
    except ValueError as e:
        return {"error": str(e)}
    out = {
        "network": str(net.network_address), "prefix_length": net.prefixlen, "netmask": str(net.netmask),
        "total_addresses": net.num_addresses, "is_private": net.is_private, "version": net.version,
    }
    if net.version == 4:
        out["broadcast"] = str(net.broadcast_address)
        usable = max(net.num_addresses - 2, 0) if net.prefixlen < 31 else net.num_addresses
        out["usable_hosts"] = usable
        if net.prefixlen < 31:
            out["first_host"], out["last_host"] = str(net.network_address + 1), str(net.broadcast_address - 1)
    return out


def cidr_overlap(cidr_a: str, cidr_b: str) -> dict:
    """Do two CIDRs overlap? (Pod/Service/Node/VPN ranges overlapping is a classic outage cause.)"""
    try:
        a = ipaddress.ip_network(str(cidr_a).strip(), strict=False)
        b = ipaddress.ip_network(str(cidr_b).strip(), strict=False)
    except ValueError as e:
        return {"error": str(e)}
    if a.version != b.version:
        return {"overlaps": False, "note": "different IP versions"}
    overlaps = a.overlaps(b)
    out = {"overlaps": overlaps, "a": str(a), "b": str(b)}
    if overlaps:
        out["relation"] = "identical" if a == b else ("a contains b" if a.supernet_of(b) else "b contains a" if b.supernet_of(a) else "partial")
    return out


def port_info(port) -> dict:
    """What usually runs on a TCP/UDP port (Kubernetes + common infra)."""
    try:
        p = int(str(port).strip().split("/")[0])
    except ValueError:
        return {"error": f"invalid port: {port!r}"}
    if not 0 < p < 65536:
        return {"error": "port must be 1-65535"}
    if p in WELL_KNOWN_PORTS:
        return {"port": p, "usually": WELL_KNOWN_PORTS[p]}
    if 30000 <= p <= 32767:
        return {"port": p, "usually": "Kubernetes NodePort range (default 30000-32767)"}
    return {"port": p, "usually": "no well-known assignment in the built-in table"}
