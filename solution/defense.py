"""
Your defense. Implement register(ctx) and a handler per event type.
See ../README.md for the full interface + toolkit reference, and
../RULES.md before you start.
"""
from api import Verdict


def _result_error(result):
    """Treat failed RPCs as unavailable evidence, not as pipeline faults."""
    return not isinstance(result, dict) or "error" in result


def _verdict(alert, pillar, reasons=None, confidence=1.0):
    return Verdict(
        alert=alert,
        confidence=confidence,
        reason="; ".join(reasons or []),
        pillar=pillar,
    )


def register(ctx):
    ctx.on("data_batch", check_data_batch)
    ctx.on("contract_checkpoint", check_contract_checkpoint)
    ctx.on("lineage_run", check_lineage_run)
    ctx.on("feature_materialization", check_feature_materialization)
    ctx.on("embedding_batch", check_embedding_batch)


def check_data_batch(payload, ctx):
    profile = ctx.tools.batch_profile(payload["batch_id"])
    if _result_error(profile):
        return _verdict(False, "checks", ["batch profile unavailable"], 0.0)

    baseline = ctx.baseline
    rows = profile["row_count"]
    null_rate = profile.get("null_rate", {}).get("customer_id", 0.0)
    mean_amount = profile["mean_amount"]
    staleness = profile["staleness_min"]

    reasons = []
    if rows < baseline["row_count_min"] or rows > baseline["row_count_max"]:
        reasons.append("row count outside clean range")
    if null_rate > baseline["null_rate_max"]:
        reasons.append("customer_id null rate too high")
    if (mean_amount < baseline["mean_amount_min"] or
            mean_amount > baseline["mean_amount_max"]):
        reasons.append("mean amount outside clean range")
    if staleness > baseline["staleness_min_max"]:
        reasons.append("batch is stale")

    # The published two-sided ranges are mean +/- 3 sigma.  Dividing by
    # their half-width puts a boundary observation at 1.0.  This catches
    # coordinated moderate movement without making any one noisy dimension
    # independently sensitive.
    row_center = (baseline["row_count_min"] + baseline["row_count_max"]) / 2.0
    row_half_width = (baseline["row_count_max"] - baseline["row_count_min"]) / 2.0
    amount_center = (baseline["mean_amount_min"] + baseline["mean_amount_max"]) / 2.0
    amount_half_width = (baseline["mean_amount_max"] - baseline["mean_amount_min"]) / 2.0

    row_distance = abs(rows - row_center) / row_half_width
    amount_distance = abs(mean_amount - amount_center) / amount_half_width
    null_distance = null_rate / baseline["null_rate_max"]
    stale_distance = staleness / baseline["staleness_min_max"]
    composite = (
        row_distance ** 2
        + amount_distance ** 2
        + null_distance ** 2
        + stale_distance ** 2
    ) ** 0.5

    component_distances = (
        row_distance, amount_distance, null_distance, stale_distance
    )
    # The published limits are approximately 3-sigma boundaries.  Screening
    # at about 2 sigma is a principled recall-oriented choice: the score makes
    # one recovered fault worth several false alerts, while the composite
    # guard prevents weak noise across all dimensions from firing by itself.
    two_sided_warning = max(row_distance, amount_distance) >= (2.0 / 3.0)
    one_sided_warning = max(null_distance, stale_distance) >= 0.80
    if not reasons and (
        two_sided_warning or one_sided_warning or composite > 0.95
    ):
        reasons.append("batch metrics crossed the two-sigma warning band")

    confidence = 0.95 if reasons and composite >= 1.0 else 0.75
    return _verdict(bool(reasons), "checks", reasons, confidence if reasons else 1.0)


def check_contract_checkpoint(payload, ctx):
    diff = ctx.tools.contract_diff(
        payload["contract_id"], payload["checkpoint_batch_id"]
    )
    if _result_error(diff):
        return _verdict(False, "contracts", ["contract diff unavailable"], 0.0)

    reasons = []
    violations = diff.get("violations", [])
    if violations:
        reasons.append("contract violations: " + ", ".join(violations))

    freshness_limit = ctx.baseline["freshness_delay_max_min"]
    declared = payload.get("declared_sla", {}).get("freshness_min")
    if isinstance(declared, (int, float)):
        freshness_limit = min(freshness_limit, declared)
    if diff["freshness_delay_min"] > freshness_limit:
        reasons.append("freshness SLA exceeded")

    return _verdict(bool(reasons), "contracts", reasons)


def check_lineage_run(payload, ctx):
    graph = ctx.tools.lineage_graph_slice(payload["run_id"])
    if _result_error(graph):
        return _verdict(False, "lineage", ["lineage slice unavailable"], 0.0)

    reasons = []
    if graph.get("actual_downstream_count", 0) == 0:
        reasons.append("orphan output")

    expected_upstream = {"raw.orders", "raw.customers"}
    actual_upstream = set(graph.get("actual_upstream") or [])
    missing = sorted(expected_upstream - actual_upstream)
    if missing:
        reasons.append("missing upstream: " + ", ".join(missing))

    runtime_limit = 0.85 * ctx.baseline["lineage_duration_ms_max"]
    if graph["duration_ms"] > runtime_limit:
        reasons.append("lineage runtime exceeded clean range")

    return _verdict(bool(reasons), "lineage", reasons)


def check_feature_materialization(payload, ctx):
    drift = ctx.tools.feature_drift(payload["feature_view"], payload["batch_id"])
    if _result_error(drift):
        return _verdict(False, "ai_infra", ["feature drift unavailable"], 0.0)

    # Use a relative safety margin over the published clean-stream boundary,
    # rather than a phase-specific absolute cut point.  This preserves some
    # tolerance for natural variation while transferring to differently
    # scaled future baselines.
    threshold = 1.10 * ctx.baseline["feature_mean_shift_sigma_max"]
    shifted = drift["mean_shift_sigma"] >= threshold
    reasons = ["training-serving feature skew"] if shifted else []
    return _verdict(shifted, "ai_infra", reasons, 0.95 if shifted else 1.0)


def check_embedding_batch(payload, ctx):
    drift = ctx.tools.embedding_drift(
        payload["corpus"], payload["chunk_batch_id"]
    )
    if _result_error(drift):
        return _verdict(False, "ai_infra", ["embedding drift unavailable"], 0.0)

    reasons = []
    # Look just inside the conservative 3-sigma limits.  Relative margins are
    # portable across corpora and avoid thresholds fitted to one phase.
    centroid_limit = 0.85 * ctx.baseline["embedding_centroid_shift_max"]
    age_limit = 0.82 * ctx.baseline["corpus_avg_doc_age_days_max"]
    if drift["centroid_shift"] >= centroid_limit:
        reasons.append("embedding centroid drift")
    if drift["avg_doc_age_days"] >= age_limit:
        reasons.append("RAG corpus is stale")

    return _verdict(bool(reasons), "ai_infra", reasons)
