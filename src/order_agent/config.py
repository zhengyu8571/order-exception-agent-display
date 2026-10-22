from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql://order_agent:order_agent_local@127.0.0.1:55432/order_agent"
    )
    order_service_url: str = "http://127.0.0.1:8101"
    operator_token: SecretStr = SecretStr("local-operator")
    supervisor_token: SecretStr = SecretStr("local-supervisor")
    service_token: SecretStr = SecretStr("local-service")
    planner_mode: Literal["demo", "llm"] = "demo"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    enable_faults: bool = False
    llm_provider: str = "openai-compatible"
    model_max_calls: int = Field(default=8, ge=1, le=30)
    model_input_budget: int = Field(default=16000, ge=1000)
    model_output_budget: int = Field(default=6000, ge=100)
    model_output_per_call: int = Field(default=1600, ge=100)
    model_timeout_seconds: float = Field(default=45, gt=0, le=120)
    llm_streaming: bool = False
    llm_enable_thinking: bool = False
    llm_trust_env: bool = True


@lru_cache
def settings() -> Settings:
    return Settings()
