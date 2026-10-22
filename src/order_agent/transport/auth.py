import secrets

from fastapi import Depends, Header, HTTPException

from order_agent.config import settings


def identity(authorization: str = Header(default="")) -> str:
    config = settings()
    if secrets.compare_digest(
        authorization, "Bearer " + config.supervisor_token.get_secret_value()
    ):
        return "supervisor"
    if secrets.compare_digest(
        authorization, "Bearer " + config.operator_token.get_secret_value()
    ):
        return "operator"
    raise HTTPException(401, "请输入有效的操作员或主管访问令牌")


def supervisor(role: str = Depends(identity)):
    if role != "supervisor":
        raise HTTPException(403, "只有主管可以审批和模拟业务变化")
    return role
