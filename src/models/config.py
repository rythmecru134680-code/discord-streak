"""Configuration models using Pydantic."""

from typing import Annotated, Final, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Status = Literal["online", "idle", "dnd"]

GATEWAY_URL: Final[str] = "wss://gateway.discord.gg/?v=10&encoding=json"
API_URL: Final[str] = "https://discord.com/api/v10"


class Server(BaseModel):
    """Discord server configuration with guild and channel IDs."""

    guild_id: Annotated[str, Field(min_length=1)]
    channel_id: Annotated[str, Field(min_length=1)]

    @field_validator("guild_id", "channel_id")
    @classmethod
    def validate_numeric_id(cls, v: str) -> str:
        """Ensure IDs contain only digits."""
        if not v.isdigit():
            msg = "ID must contain only digits"
            raise ValueError(msg)
        return v


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DISCORD_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    token: Annotated[str, Field(min_length=1)]
    status: Status = "online"
    servers_raw: Annotated[str, Field(alias="DISCORD_SERVERS", min_length=1)]

    spam_channel_id: str = ""
    spam_message: str = "🔥 Keeping the streak alive"
    spam_interval: float = 0.5
    spam_enabled: bool = False

    @property
    def servers(self) -> list[Server]:
        """Parse servers from comma-separated guild_id:channel_id pairs."""
        servers: list[Server] = []
        for pair in self.servers_raw.split(","):
            pair = pair.strip()
            if ":" not in pair:
                msg = f"Invalid server format: {pair} (expected guild_id:channel_id)"
                raise ValueError(msg)
            guild_id, channel_id = pair.split(":", 1)
            servers.append(
                Server(guild_id=guild_id.strip(), channel_id=channel_id.strip())
            )
        return servers
