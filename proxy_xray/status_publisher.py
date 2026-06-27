from .slot_manager import slot_alive, slot_candidates, slot_public_status, xray_api_status_for_slot
from .status import public_candidate, set_status, status_candidate_fields


def set_runtime_status(candidates, args, active_slot, standby_slot):
    active_pool = slot_candidates(active_slot)
    standby_pool = slot_candidates(standby_slot)
    active_api = xray_api_status_for_slot(active_slot, args)
    standby_api = xray_api_status_for_slot(standby_slot, args)
    set_status(
        **status_candidate_fields(candidates, args.standby_max_age),
        xray_running=slot_alive(active_slot),
        active_pool=[public_candidate(candidate) for candidate in active_pool],
        active_backend=slot_public_status(active_slot),
        standby_pool=[public_candidate(candidate) for candidate in standby_pool],
        hot_standby=slot_public_status(standby_slot),
        active_path=active_api,
        active_observatory=active_api,
        standby_observatory=standby_api,
    )
