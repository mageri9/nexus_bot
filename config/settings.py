from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr
from typing import List


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    REDIS_URL: str = "redis://localhost:6379/0"
    DEBUG: bool = False
    ADMIN_IDS: str = "0"  # ID администраторов через запятую

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def bot_token_str(self) -> str:
        return self.BOT_TOKEN.get_secret_value()

    @property
    def admin_id_list(self) -> List[int]:
        return [int(x.strip()) for x in self.ADMIN_IDS.split(",") if x.strip()]


settings = Settings()