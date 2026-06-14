"""Core engine for Discord client and server management."""

import asyncio
import json
import random
import time
from http import HTTPStatus
from typing import Any, Final

import httpx
import websockets  # pyright: ignore[reportMissingImports]

from src import __metadata__
from src.models.config import API_URL, GATEWAY_URL, Server, Settings, Status
from src.models.results import SessionState, User
from src.utils.logger import log

# Activity configuration
VERSION: Final[str] = __metadata__["version"]
ACTIVITY_NAME: Final[str] = f"discord-streak v{VERSION}"

# Reconnection settings
BASE_DELAY: Final[float] = 1.0
MAX_DELAY: Final[float] = 60.0
JITTER_FACTOR: Final[float] = 0.1


def generate_client_properties(index: int) -> dict[str, Any]:
    """Generate unique client properties for each connection (15 unique combos)."""
    os_list = ["Windows", "Linux", "Mac OS X"]
    browser_list = ["Chrome", "Firefox", "Safari", "Edge", "Discord Client"]

    os_name = os_list[index % len(os_list)]
    browser = browser_list[index // len(os_list) % len(browser_list)]

    user_agents = {
        "Chrome": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Firefox": "Mozilla/5.0 ({os}; rv:127.0) Gecko/20100101 Firefox/127.0",
        "Safari": "Mozilla/5.0 ({os}) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Edge": "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        "Discord Client": "Mozilla/5.0 ({os})",
    }

    os_ua_map = {
        "Windows": "Windows NT 10.0; Win64; x64",
        "Linux": "X11; Linux x86_64",
        "Mac OS X": "Macintosh; Intel Mac OS X 10_15_7",
    }

    ua = user_agents.get(browser, "").replace("{os}", os_ua_map.get(os_name, ""))

    return {
        "os": os_name,
        "browser": browser,
        "device": "",
        "system_locale": "en-US",
        "browser_user_agent": ua,
        "browser_version": "125.0.0.0",
        "os_version": "10",
        "referrer": "",
        "referring_domain": "",
        "referrer_current": "",
        "referring_domain_current": "",
        "release_channel": "stable",
        "client_build_number": 268847,
        "client_event_source": None,
    }


def calculate_backoff(attempt: int) -> float:
    """Exponential backoff: 1s -> 2s -> 4s -> ... -> 60s max."""
    delay = min(BASE_DELAY * (2**attempt), MAX_DELAY)
    jitter = random.uniform(0, delay * JITTER_FACTOR)
    return delay + jitter


class DiscordClient:
    """Discord Gateway WebSocket client."""

    def __init__(
        self, token: str, status: Status, client_index: int, start_time: int
    ) -> None:
        self.token = token
        self.status = status
        self.client_index = client_index
        self.properties = generate_client_properties(index=client_index)
        self.start_time = start_time

    async def get_user(self) -> User | None:
        """Validate token and get user information."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{API_URL}/users/@me",
                headers={"Authorization": self.token},
            )
            if resp.status_code == 200:
                return resp.json()
            return None

    async def keep_online(self, server: Server, session: SessionState) -> None:
        """Maintain connection for a single server."""
        ua = self.properties.get("browser_user_agent", "")
        extra_headers = {
            "Origin": "https://discord.com",
            "User-Agent": ua or "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with websockets.connect(
            GATEWAY_URL, max_size=2**23, extra_headers=extra_headers
        ) as ws:
            hello = json.loads(await ws.recv())
            heartbeat_interval: float = hello["d"]["heartbeat_interval"] / 1000

            log(
                "info",
                f"[Server {self.client_index + 1}] Connected to Gateway "
                f"(heartbeat: {heartbeat_interval:.1f}s)",
            )

            # Identify with correct user token format:
            # - capabilities: 16381 (all bits except NO_AFFINE_USER_IDS)
            # - presence: status=unknown, empty activities (per Discord docs for user tokens)
            # - client_state with api_code_version and guild_versions
            identify = {
                "op": 2,
                "d": {
                    "token": self.token,
                    "capabilities": 16381,
                    "properties": self.properties,
                    "compress": False,
                    "presence": {
                        "status": "unknown",
                        "since": 0,
                        "activities": [],
                        "afk": False,
                    },
                    "client_state": {
                        "api_code_version": 0,
                        "guild_versions": {},
                    },
                },
            }
            await ws.send(json.dumps(identify))

            # Wait for READY
            session_id = None
            identified = False
            while not identified:
                msg = json.loads(await ws.recv())
                op = msg.get("op")
                t = msg.get("t")
                if op == 0 and t == "READY":
                    session_id = msg.get("d", {}).get("session_id")
                    identified = True
                    log(
                        "info",
                        f"[Server {self.client_index + 1}] Session ready "
                        f"(session_id: {session_id})",
                    )
                elif op == 9:
                    log(
                        "warn",
                        f"[Server {self.client_index + 1}] Invalid session, retrying...",
                    )
                    await asyncio.sleep(1)
                    await ws.send(json.dumps(identify))

            # Mark as connected (for backoff reset)
            session.mark_connected()

            # Send actual presence update (op 3) after identify
            presence_update = {
                "op": 3,
                "d": {
                    "status": self.status,
                    "since": 0,
                    "activities": [
                        {
                            "name": ACTIVITY_NAME,
                            "type": 0,
                        }
                    ],
                    "afk": False,
                },
            }
            await ws.send(json.dumps(presence_update))
            log(
                "info",
                f"[Server {self.client_index + 1}] Presence set to {self.status}",
            )

            # Join voice channel
            voice_state = {
                "op": 4,
                "d": {
                    "guild_id": server.guild_id,
                    "channel_id": server.channel_id,
                    "self_mute": True,
                    "self_deaf": True,
                },
            }
            await ws.send(json.dumps(voice_state))
            log(
                "info",
                f"[Server {self.client_index + 1}] Voice state update sent: "
                f"channel {server.channel_id} in guild {server.guild_id}",
            )

            # Heartbeat loop with event processing
            async def heartbeat():
                while True:
                    await ws.send(json.dumps({"op": 1, "d": None}))
                    await asyncio.sleep(heartbeat_interval)

            async def message_reader():
                while True:
                    msg = json.loads(await ws.recv())
                    op = msg.get("op")
                    t = msg.get("t")

                    if op == 9:
                        log(
                            "warn",
                            f"[Server {self.client_index + 1}] Session invalidated, reconnecting...",
                        )
                        raise websockets.ConnectionClosed(
                            3000, "Session invalidated"
                        )
                    elif op == 7:
                        log(
                            "warn",
                            f"[Server {self.client_index + 1}] Reconnect requested, reconnecting...",
                        )
                        raise websockets.ConnectionClosed(
                            3000, "Reconnect requested"
                        )
                    elif op == 0 and t == "VOICE_STATE_UPDATE":
                        vs = msg.get("d", {})
                        log(
                            "info",
                            f"[Server {self.client_index + 1}] Voice state: "
                            f"channel={vs.get('channel_id')}, "
                            f"session_id={vs.get('session_id')}",
                        )
                    elif op == 0 and t == "VOICE_SERVER_UPDATE":
                        vs = msg.get("d", {})
                        log(
                            "info",
                            f"[Server {self.client_index + 1}] Voice server: "
                            f"endpoint={vs.get('endpoint')}",
                        )

            await asyncio.gather(heartbeat(), message_reader())


class HealthServer:
    """HTTP health check server."""

    def __init__(self, port: int = 8080) -> None:
        self.port = port

    async def handle_request(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle incoming HTTP request."""
        await reader.readline()
        response = (
            f"HTTP/1.1 {HTTPStatus.OK} OK\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: 2\r\n"
            "\r\n"
            "OK"
        )
        writer.write(response.encode())
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def start(self) -> None:
        """Start the health check server."""
        server = await asyncio.start_server(
            self.handle_request,
            "0.0.0.0",
            self.port,  # noqa: S104
        )
        log("info", f"Health server running on port {self.port}")
        async with server:
            await server.serve_forever()


async def run_server_client(
    token: str,
    status: Status,
    server: Server,
    client_index: int,
    start_time: int,
) -> None:
    """Manage connection for a single server with reconnection."""
    session = SessionState()
    client = DiscordClient(token, status, client_index, start_time)
    attempt = 0

    while True:
        session.mark_disconnected()

        try:
            await client.keep_online(server, session)
        except (
            websockets.ConnectionClosed,
            websockets.WebSocketException,
            OSError,
        ) as e:
            if session.connected:
                attempt = 0

            delay = calculate_backoff(attempt)
            error_msg = str(e) or type(e).__name__
            log("warn", f"[Server {client_index + 1}] Connection error: {error_msg}")
            log(
                "info",
                f"[Server {client_index + 1}] Reconnecting in {delay:.1f}s "
                f"(attempt {attempt + 1})...",
            )
            session.mark_reconnecting()
            await asyncio.sleep(delay)
            attempt += 1


async def spam_messages(token: str, channel_id: str, message: str, interval: float) -> None:
    """Send messages to a Discord channel repeatedly."""
    while True:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{API_URL}/channels/{channel_id}/messages",
                    headers={
                        "Authorization": token,
                        "Content-Type": "application/json",
                    },
                    json={"content": message},
                )
                if resp.status_code == 200:
                    log("info", f"[Spam] Message sent to channel {channel_id}")
                else:
                    log(
                        "warn",
                        f"[Spam] Failed to send message: HTTP {resp.status_code}",
                    )
        except httpx.HTTPError as e:
            log("warn", f"[Spam] HTTP error: {e}")

        await asyncio.sleep(interval)


async def run_all(settings: Settings) -> None:
    """Run all server connections and health server."""
    start_time = int(time.time() * 1000)

    health_server = HealthServer()
    tasks: list[asyncio.Task[None]] = [asyncio.create_task(health_server.start())]

    for i, server in enumerate(settings.servers):
        task = asyncio.create_task(
            run_server_client(settings.token, settings.status, server, i, start_time)
        )
        tasks.append(task)

    if settings.spam_enabled and settings.spam_channel_id:
        spam_task = asyncio.create_task(
            spam_messages(
                settings.token,
                settings.spam_channel_id,
                settings.spam_message,
                settings.spam_interval,
            )
        )
        tasks.append(spam_task)
        log(
            "info",
            f"[Spam] Enabled (interval: {settings.spam_interval}s, "
            f"channel: {settings.spam_channel_id})",
        )

    await asyncio.gather(*tasks)
