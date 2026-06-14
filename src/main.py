"""Main entry point for discord-streak."""

import asyncio
import sys

from pydantic import ValidationError

from src.engine.runner import DiscordClient, run_all
from src.models.config import Settings
from src.utils.errors import AuthenticationError
from src.utils.logger import log


async def main() -> None:
    """Initialize and run the Discord streak bot."""
    try:
        settings = Settings()  # pyright: ignore[reportCallIssue]
    except ValidationError as e:
        for error in e.errors():
            field = error["loc"][0] if error["loc"] else "unknown"
            log("error", f"Configuration error: {field} - {error['msg']}")
        sys.exit(1)

    # Validate token (start_time=0 since we only call get_user)
    client = DiscordClient(settings.token, settings.status, 0, 0)
    user = await client.get_user()

    if not user:
        log("error", "Invalid token")
        raise AuthenticationError("Invalid Discord token")

    log("info", f"Logged in as {user['username']} ({user['id']})")
    log("info", f"Status: {settings.status}")
    log("info", f"Servers: {len(settings.servers)}")
    log("info", f"Spam enabled: {settings.spam_enabled}, channel: {settings.spam_channel_id}, interval: {settings.spam_interval}")

    await run_all(settings)


def run() -> None:
    """Run the main async function."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("info", "Shutting down...")
    except AuthenticationError:
        sys.exit(1)


if __name__ == "__main__":
    run()
