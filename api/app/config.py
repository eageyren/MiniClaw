from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)
    database_url: str = "postgresql+psycopg://miniclaw:miniclaw-local@localhost:5432/miniclaw"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = "gpt-4o-mini"
    family_id: str = "local-family"
    member_id: str = "local-elder"
    family_timezone: str = "Asia/Shanghai"
    family_admin_name: str = "家属"
    sms_gateway_url: str = ""
    sms_gateway_token: str = ""
    push_vapid_public_key: str = ""
    push_vapid_private_key: str = ""
    push_vapid_subject: str = "mailto:admin@example.com"
    cost_stt_per_minute_usd: float = 0.0
    cost_input_per_million_tokens_usd: float = 0.0
    cost_output_per_million_tokens_usd: float = 0.0
    cost_tts_per_million_characters_usd: float = 0.0
    monthly_cost_limit_usd: float = 0.0
    checkpoint_schema: str = "miniclaw_checkpoints"
    stt_provider: str = "openai_compatible"
    stt_model: str = "whisper-1"
    tts_provider: str = "openai_compatible"
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "alloy"
    context_token_budget: int = 6000
    context_keep_turns: int = 8
    context_summary_max_tokens: int = 1000
    checkpoint_ttl_days: int = 14
    app_env: str = "development"

settings = Settings()
