from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr


class Settings(BaseSettings):
    BOT_TOKEN: SecretStr
    REDIS_URL: str = "redis://localhost:6379/0"
    DEBUG: bool = False

    # Настройки загрузки из файла окружения
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    @property
    def bot_token_str(self) -> str:
        return self.BOT_TOKEN.get_secret_value()


settings = Settings()