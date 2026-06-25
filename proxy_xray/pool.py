import time

from .vless import (
    candidate_is_live,
    candidate_is_preferred_region,
    candidate_is_quarantined,
    native_candidate_order,
)


def candidate_uri(candidate):
    return candidate.get("uri") if candidate else None


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


def append_seed(result, seen_uris, seed):
    uri = candidate_uri(seed)
    if not seed or not uri or uri in seen_uris:
        return
    if candidate_is_quarantined(seed):
        return
    result.append(seed)
    seen_uris.add(uri)


def select_candidate_pool(
    candidates,
    size=1,
    exclude_uris=None,
    live_only=False,
    max_age=0,
    now=None,
):
    size = normalize_pool_size(size)
    if size <= 0:
        return []

    seen_uris = set(exclude_uris or [])
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
            result.append(candidate)
            seen_uris.add(uri)
    return result


def select_active_pool(candidates, active_candidate=None, size=1, now=None):
    size = normalize_pool_size(size)
    result = []
    seen_uris = set()
    append_seed(result, seen_uris, active_candidate)
    if len(result) >= size:
        return result[:size]
    result.extend(
        select_candidate_pool(
            candidates,
            size=size - len(result),
            exclude_uris=seen_uris,
            live_only=False,
            now=now,
        )
    )
    return result


def select_standby_pool(candidates, active_pool=None, standby_candidate=None, size=1, max_age=600, now=None):
    size = normalize_pool_size(size)
    result = []
    excluded = {candidate_uri(candidate) for candidate in active_pool or [] if candidate_uri(candidate)}
    seen_uris = set(excluded)

    append_seed(result, seen_uris, standby_candidate)
    if len(result) >= size:
        return result[:size]

    fresh = select_candidate_pool(
        candidates,
        size=size - len(result),
        exclude_uris=seen_uris,
        live_only=True,
        max_age=max_age,
        now=now,
    )
    result.extend(fresh)
    seen_uris.update(candidate_uri(candidate) for candidate in fresh if candidate_uri(candidate))
    if len(result) >= size:
        return result

    last_known_good = select_candidate_pool(
        candidates,
        size=size - len(result),
        exclude_uris=seen_uris,
        live_only=True,
        max_age=0,
        now=now,
    )
    result.extend(last_known_good)
    seen_uris.update(candidate_uri(candidate) for candidate in last_known_good if candidate_uri(candidate))
    if len(result) >= size:
        return result

    result.extend(
        select_candidate_pool(
            candidates,
            size=size - len(result),
            exclude_uris=seen_uris,
            live_only=False,
            now=now,
        )
    )
    return result
