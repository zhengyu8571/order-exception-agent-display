from order_agent.domain.contracts import Candidate


def validate_candidate(candidate: Candidate, facts: dict) -> list[str]:
    """Hard constraints are independent of the proposing model."""
    order, stock, arrivals = facts["order"], facts["stock"], facts["arrivals"]
    errors: list[str] = []
    if order["status"] != "EXCEPTION":
        errors.append("订单当前不处于异常待处理状态")
    if candidate.local_quantity > stock["local_quantity"]:
        errors.append("本仓可用库存不足")
    if candidate.transfer_quantity > stock["remote_quantity"]:
        errors.append("外仓可用库存不足")
    if candidate.purchase_quantity > arrivals["quantity"]:
        errors.append("采购预计到货数量不足")
    total = (
        candidate.local_quantity
        + candidate.transfer_quantity
        + candidate.purchase_quantity
    )
    if total != order["required_quantity"]:
        errors.append("方案数量之和必须等于订单需求")
    minimum_days = max(
        stock["transfer_days"] if candidate.transfer_quantity else 0,
        arrivals["arrival_days"] if candidate.purchase_quantity else 0,
    )
    if candidate.completion_days < minimum_days:
        errors.append("完成时间早于调拨或采购可用时间")
    if candidate.completion_days > order["due_days"]:
        errors.append("方案超过客户允许交期")
    if candidate.split_delivery and not order["allow_split"]:
        errors.append("客户尚未同意分批交付")
    if candidate.first_delivery_quantity > candidate.local_quantity:
        errors.append("首批即时交付不能超过本仓可用数量")
    if not candidate.split_delivery and candidate.first_delivery_quantity != 0:
        errors.append("整单交付方案不能声明即时首批交付")
    if (
        candidate.split_delivery
        and not 0 < candidate.first_delivery_quantity < order["required_quantity"]
    ):
        errors.append("分批交付首批数量不合法")
    return errors
