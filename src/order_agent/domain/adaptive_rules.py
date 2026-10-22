"""Validate model resource combinations; compute availability, dates and costs here."""

from datetime import date

from order_agent.domain.decisions import AdaptiveCandidate, RuleIssue
from order_agent.domain.scheduling import planning_start, transfer_available_on


def validate_plan(
    candidate: AdaptiveCandidate, context: dict, evidence: dict | None = None
):
    issues = []
    order, policy = context["order"], context["policy"]
    start = planning_start(context)
    available = [(start, candidate.local_quantity)]
    cost_cents = 0

    def issue(code, field, expected, actual, evidence_id=""):
        issues.append(
            RuleIssue(
                code=code,
                field=field,
                expected=str(expected),
                actual=str(actual),
                evidence_id=evidence_id,
            ).model_dump()
        )

    def cite(key, kind, resource_id):
        if evidence is None:
            return
        record = evidence.get(key)
        if kind == "route":
            resource = next(
                row for row in context["warehouses"] if row["id"] == resource_id
            )
            expected_versions = {
                "stock": resource.get("version", 1),
                "route": resource.get("route_version", 1),
            }
        elif kind == "batch":
            resource = next(
                row for row in context["purchase_batches"] if row["id"] == resource_id
            )
            expected_versions = {"batch": resource.get("version", 1)}
        else:
            expected_versions = {"policy": policy.get("version", 1)}
        if (
            not record
            or record.get("kind") != kind
            or record.get("resource_id") != resource_id
            or record.get("order_id") != order["id"]
            or record.get("order_version") != order["version"]
            or record.get("resource_versions", dict.fromkeys(expected_versions, 1))
            != expected_versions
        ):
            issue(
                "MISSING_OR_STALE_EVIDENCE",
                "evidence_id",
                f"current {kind}/{resource_id}",
                key,
                key,
            )

    if candidate.local_quantity > context["local_quantity"]:
        issue(
            "LOCAL_STOCK_EXCEEDED",
            "local_quantity",
            context["local_quantity"],
            candidate.local_quantity,
        )
    warehouses = {row["id"]: row for row in context["warehouses"]}
    batches = {row["id"]: row for row in context["purchase_batches"]}
    used = set()
    for index, allocation in enumerate(candidate.transfers):
        field = f"transfers[{index}]"
        warehouse = warehouses.get(allocation.warehouse_id)
        if not warehouse or allocation.warehouse_id in used:
            issue(
                "UNKNOWN_OR_DUPLICATE_WAREHOUSE",
                field,
                "known unique warehouse",
                allocation.warehouse_id,
            )
            continue
        used.add(allocation.warehouse_id)
        cite(allocation.evidence_id, "route", allocation.warehouse_id)
        if allocation.quantity > warehouse["available_quantity"]:
            issue(
                "WAREHOUSE_STOCK_EXCEEDED",
                field + ".quantity",
                warehouse["available_quantity"],
                allocation.quantity,
                allocation.evidence_id,
            )
        available.append(
            (
                transfer_available_on(context, warehouse["transfer_days"]),
                allocation.quantity,
            )
        )
        cost_cents += allocation.quantity * warehouse["unit_cost_cents"]
    used = set()
    for index, purchase in enumerate(candidate.purchase_allocations):
        field = f"purchase_allocations[{index}]"
        batch = batches.get(purchase.batch_id)
        if not batch or purchase.batch_id in used:
            issue(
                "UNKNOWN_OR_DUPLICATE_BATCH",
                field,
                "known unique batch",
                purchase.batch_id,
            )
            continue
        used.add(purchase.batch_id)
        cite(purchase.evidence_id, "batch", purchase.batch_id)
        if purchase.quantity > batch["quantity"] or batch["status"] != "EXPECTED":
            issue(
                "PURCHASE_NOT_AVAILABLE",
                field,
                f"EXPECTED quantity <= {batch['quantity']}",
                purchase.quantity,
                purchase.evidence_id,
            )
        available.append((date.fromisoformat(batch["arrival_date"]), purchase.quantity))
        latest = (
            order["due_date"]
            if candidate.delivery_mode == "FULL"
            else policy["last_delivery_date"]
        )
        if date.fromisoformat(batch["arrival_date"]) > date.fromisoformat(latest):
            issue(
                "PURCHASE_ARRIVAL_TOO_LATE",
                field,
                latest,
                batch["arrival_date"],
                purchase.evidence_id,
            )
    allocated = (
        candidate.local_quantity
        + sum(row.quantity for row in candidate.transfers)
        + sum(row.quantity for row in candidate.purchase_allocations)
    )
    if allocated != order["required_quantity"]:
        issue(
            "ALLOCATION_QUANTITY_MISMATCH",
            "allocations",
            order["required_quantity"],
            allocated,
        )
    if sum(row.quantity for row in candidate.deliveries) != order["required_quantity"]:
        issue(
            "DELIVERY_QUANTITY_MISMATCH",
            "deliveries",
            order["required_quantity"],
            sum(row.quantity for row in candidate.deliveries),
        )
    if candidate.delivery_mode == "FULL" and len(candidate.deliveries) != 1:
        issue("FULL_REQUIRES_ONE_DELIVERY", "deliveries", 1, len(candidate.deliveries))
    if candidate.delivery_mode == "SPLIT":
        if not policy["allow_split"] or len(candidate.deliveries) < 2:
            issue(
                "SPLIT_NOT_PERMITTED",
                "delivery_mode",
                "customer permission and >=2 deliveries",
                candidate.delivery_mode,
            )
        cite(f"policy:{order['id']}:v{order['version']}", "policy", order["id"])
        if candidate.deliveries[0].quantity < policy["minimum_first_quantity"]:
            issue(
                "FIRST_DELIVERY_TOO_SMALL",
                "deliveries[0].quantity",
                policy["minimum_first_quantity"],
                candidate.deliveries[0].quantity,
            )
    cumulative = 0
    previous = None
    for index, delivery in enumerate(candidate.deliveries):
        field = f"deliveries[{index}]"
        try:
            when = date.fromisoformat(delivery.delivery_date)
        except ValueError:
            issue(
                "INVALID_DATE",
                field + ".delivery_date",
                "calendar date",
                delivery.delivery_date,
            )
            continue
        if when < start or (previous is not None and when <= previous):
            issue(
                "DELIVERY_DATE_ORDER",
                field,
                f"increasing dates >= {start.isoformat()}",
                delivery.delivery_date,
            )
        previous = when
        deadline = (
            order["due_date"]
            if candidate.delivery_mode == "FULL" or index == 0
            else policy["last_delivery_date"]
        )
        if when > date.fromisoformat(deadline):
            issue(
                "DELIVERY_AFTER_DEADLINE",
                field + ".delivery_date",
                deadline,
                delivery.delivery_date,
            )
        cumulative += delivery.quantity
        released = sum(quantity for arrival, quantity in available if arrival <= when)
        if cumulative > released:
            late_purchase = any(
                date.fromisoformat(batches[row.batch_id]["arrival_date"]) > when
                for row in candidate.purchase_allocations
                if row.batch_id in batches
            )
            issue(
                "PURCHASE_ARRIVAL_TOO_LATE" if late_purchase else "RESOURCE_NOT_READY",
                field,
                f"available >= {cumulative}",
                released,
            )
    metrics = {"transfer_cost_cents": cost_cents, "allocated_quantity": allocated}
    return issues, metrics
