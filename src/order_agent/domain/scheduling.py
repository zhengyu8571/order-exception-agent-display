"""Business dates shared by validation, observations and deterministic references."""

from datetime import date, timedelta


def planning_start(context: dict) -> date:
    return max(
        date.fromisoformat(context["as_of"]),
        date.fromisoformat(context.get("today", context["as_of"])),
    )


def transfer_available_on(context: dict, transfer_days: int) -> date:
    # 本项目没有“已发运”的业务事实；等待审批不能抵扣运输时间。
    # 只滚动调拨的最早可用日，订单交期和采购的绝对到货日不能随今天一起后移。
    return planning_start(context) + timedelta(days=transfer_days)
