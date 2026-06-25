import time

from .vless import (
    candidate_is_live,
    candidate_is_preferred_region,
    candidate_is_quarantined,
    native_candidate_order,
)


def candidate_uri(candidate):
    return candidate.get("uri") if candidate else None


def candidate_host(candidate):
    return (candidate.get("host") or "").lower() if candidate else ""


def extra_candidate_uris(candidates):
    return {candidate_uri(candidate) for candidate in candidates if candidate.get("source") == "extra" and candidate_uri(candidate)}


def normalize_pool_size(size, default=1):
    try:
        value = int(size)
    except (TypeError, ValueError):
        value = default
    return max(0, value)


def candidate_is_recent_live(candidate, max_age=0, now=None):
    if not candidate_is_live(candidate):
        return False
    if max_age <= 0:
        return True
    now = now or time.time()
    last_ok_at = candidate.get("last_ok_at")
    return bool(last_ok_at and now - float(last_ok_at) <= max_age)


def increment_host_count(host_counts, candidate):
    host = candidate_host(candidate)
    if host:
        host_counts[host] = host_counts.get(host, 0) + 1


def append_seed(result, seen_uris, host_counts, seed):
    uri = candidate_uri(seed)
    if not seed or not uri or uri in seen_uris:
        return
    if candidate_is_quarantined(seed):
        return
    result.append(seed)
    seen_uris.add(uri)
    increment_host_count(host_counts, seed)


def select_extra_reserve(
    candidates,
    seen_uris,
    host_counts,
    size=1,
    require_live=True,
    max_age=600,
    max_per_host=1,
    now=None,
):
    selected = []
    size = normalize_pool_size(size)
    if size <= 0:
        return selected
    for candidate in native_candidate_order(candidates):
        if len(selected) >= size:
            return selected
        if candidate.get("source") != "extra":
            continue
        uri = candidate_uri(candidate)
        if not uri or uri in seen_uris:
            continue
        if candidate_is_quarantined(candidate, now=now):
            continue
        if require_live and not candidate_is_recent_live(candidate, max_age=max_age, now=now):
            continue
        host = candidate_host(candidate)
        if max_per_host > 0 and host and host_counts.get(host, 0) >= max_per_host:
            continue
        selected.append(candidate)
        seen_uris.add(uri)
        increment_host_count(host_counts, candidate)
    return selected


def select_candidate_pool(
    candidates,
    size=1,
    exclude_uris=None,
    live_only=False,
    max_age=0,
    max_per_host=2,
    host_counts=None,
    now=None,
):
    size = normalize_pool_size(size)
    if size <= 0:
        return []

    seen_uris = set(exclude_uris or [])
    host_counts = dict(host_counts or {})
    result = []
    ordered = native_candidate_order(candidates)

    for preferred_only in (True, False):
        for candidate in ordered:
            if len(result) >= size:
                return result
            uri = candidate_uri(candidate)
            if not uri or uri in seen_uris:
                continue
            if preferred_only and not candidate_is_preferred_region(candidate):
                continue
            if candidate_is_quarantined(candidate):
                continue
            if live_only and not candidate_is_recent_live(candidate, max_age=max_age, now=now):
                continue
            host = candidate_host(candidate)
            if max_per_host > 0 and host and host_counts.get(host, 0) >= max_per_host:
                continue
            result.append(candidate)
            seen_uris.add(uri)
            increment_host_count(host_counts, candidate)
    return result


def select_active_pool(
    candidates,
    active_candidate=None,
    size=1,
    extra_reserve_per_slot=0,
    extra_require_live=True,
    extra_max_age=0,
    extra_max_per_host=1,
    now=None,
):
    size = normalize_pool_size(size)
    result = []
    seen_uris = set()
    host_counts = {}
    if size == 1:
        append_seed(result, seen_uris, host_counts, active_candidate)
    if len(result) >= size:
        return result[:size]
    extra_reserve = select_extra_reserve(
        candidates,
        seen_uris,
        host_counts,
        size=min(extra_reserve_per_slot, size - len(result)),
        require_live=extra_require_live,
        max_age=extra_max_age,
        max_per_host=extra_max_per_host,
        now=now,
    )
    result.extend(extra_reserve)
    if len(result) >= size:
        return result[:size]
    if extra_reserve_per_slot > 0:
        seen_uris.update(uri for uri in extra_candidate_uris(candidates) if uri not in {candidate_uri(item) for item in result})
    result.extend(
        select_candidate_pool(
            candidates,
            size=size - len(result),
            exclude_uris=seen_uris,
            host_counts=host_counts,
            live_only=False,
            now=now,
        )
    )
    return result


def select_standby_pool(
    candidates,
    active_pool=None,
    standby_candidate=None,
    size=1,
    max_age=600,
    extra_reserve_per_slot=0,
    extra_require_live=True,
    extra_max_age=0,
    extra_max_per_host=1,
    now=None,
):
    size = normalize_pool_size(size)
    result = []
    active_extra_uris = {
        candidate_uri(candidate)
        for candidate in active_pool or []
        if candidate.get("source") == "extra" and candidate_uri(candidate)
    }
    excluded = {candidate_uri(candidate) for candidate in active_pool or [] if candidate_uri(candidate)}
    seen_uris = set(excluded)
    host_counts = {}

    append_seed(result, seen_uris, host_counts, standby_candidate)
    if len(result) >= size:
        return result[:size]
    extra_reserve = select_extra_reserve(
        candidates,
        seen_uris,
        host_counts,
        size=min(extra_reserve_per_slot, size - len(result)),
        require_live=extra_require_live,
        max_age=extra_max_age,
        max_per_host=extra_max_per_host,
        now=now,
    )
    if not extra_reserve and active_extra_uris:
        extra_reserve = select_extra_reserve(
            candidates,
            seen_uris - active_extra_uris,
            host_counts,
            size=min(extra_reserve_per_slot, size - len(result)),
            require_live=extra_require_live,
            max_age=extra_max_age,
            max_per_host=extra_max_per_host,
            now=now,
        )
    result.extend(extra_reserve)
    if len(result) >= size:
        return result
    if extra_reserve_per_slot > 0:
        seen_uris.update(uri for uri in extra_candidate_uris(candidates) if uri not in {candidate_uri(item) for item in result})

    fresh = select_candidate_pool(
        candidates,
        size=size - len(result),
        exclude_uris=seen_uris,
        host_counts=host_counts,
        live_only=True,
        max_age=max_age,
        now=now,
    )
    result.extend(fresh)
    seen_uris.update(candidate_uri(candidate) for candidate in fresh if candidate_uri(candidate))
    for candidate in fresh:
        increment_host_count(host_counts, candidate)
    if len(result) >= size:
        return result

    last_known_good = select_candidate_pool(
        candidates,
        size=size - len(result),
        exclude_uris=seen_uris,
        host_counts=host_counts,
        live_only=True,
        max_age=0,
        now=now,
    )
    result.extend(last_known_good)
    seen_uris.update(candidate_uri(candidate) for candidate in last_known_good if candidate_uri(candidate))
    for candidate in last_known_good:
        increment_host_count(host_counts, candidate)
    if len(result) >= size:
        return result

    result.extend(
        select_candidate_pool(
            candidates,
            size=size - len(result),
            exclude_uris=seen_uris,
            host_counts=host_counts,
            live_only=False,
            now=now,
        )
    )
    return result
