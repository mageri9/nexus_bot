from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import List, Optional


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    REDIS_URL: str = "redis://localhost:6379/0"
    DEBUG: bool = False
    ADMIN_IDS: str = "0"

    # Конфигурация StateCollector.
    COLLECTOR_DEBOUNCE_TICKS: int = 2
    APP_ERROR_AUTO_RECOVERY_THRESHOLD: int = 60

    # Конфигурация шлюза AITUNNEL
    AITUNNEL_API_KEY: Optional[SecretStr] = None
    AITUNNEL_BASE_URL: str = "https://api.aitunnel.ru/v1/"
    AITUNNEL_MODEL: str = "gemma-4-31b-it"  # Google Gemma 4

    # Конфигурация прогнозирования рисков (ML Predictor)
    PREDICTOR_RISK_THRESHOLD: float = (
        0.6  # Порог вероятности сбоя, при котором бьем тревогу
    )
    PREDICTOR_DISCREPANCY_THRESHOLD: float = (
        0.4  # Минимальное расхождение между оценкой ML и правилами
    )

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @property
    def bot_token_str(self) -> str:
        return self.BOT_TOKEN.get_secret_value()

    @property
    def aitunnel_api_key_str(self) -> Optional[str]:
        return (
            self.AITUNNEL_API_KEY.get_secret_value() if self.AITUNNEL_API_KEY else None
        )

    @property
    def admin_id_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]


settings = Settings()