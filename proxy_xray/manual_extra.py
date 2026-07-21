def transport_priority(candidate):
    stream = candidate.get("outbound", {}).get("streamSettings", {})
    network = (stream.get("network") or "").lower()
    security = (stream.get("security") or "").lower()
    if network == "tcp" and security == "reality":
        return 0
    if network == "tcp" and security == "tls":
        return 1
    if network == "tcp":
        return 2
    return {
        "grpc": 3,
        "xhttp": 4,
        "splithttp": 5,
        "ws": 6,
        "httpupgrade": 7,
    }.get(network, 9)


def manual_extra_order(candidates):
    def key(candidate):
        quality = candidate.get("quality") if isinstance(candidate.get("quality"), dict) else {}
        checks = int(quality.get("checks") or 0)
        success_rate = float(quality.get("success_rate") if checks >= 3 else 0.5)
        consecutive_failures = int(quality.get("consecutive_failures") or 0)
        recent_failures = int(quality.get("last_24h_failures") or 0)
        latency = quality.get("latency_ewma")
        if latency is None:
            latency = candidate.get("last_latency")
        throughput = quality.get("throughput_ewma") or candidate.get("last_throughput_kbps") or 0
        return (
            consecutive_failures,
            transport_priority(candidate),
            recent_failures,
            -success_rate,
            float(latency) if latency is not None else 999999.0,
            -float(throughput),
            candidate.get("index", 999999),
        )

    return sorted((candidate for candidate in candidates if candidate.get("source") == "extra"), key=key)


def remaining_extra_candidates(candidates, failed_candidate):
    failed_uri = (failed_candidate or {}).get("uri")
    return manual_extra_order(
        candidate
        for candidate in candidates
        if candidate.get("source") == "extra" and candidate.get("uri") != failed_uri
    )
