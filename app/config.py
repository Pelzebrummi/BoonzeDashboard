"""Runtime settings from the app options file (/data/options.json) and the environment."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

MEDIA = ("strom", "gas", "wasser")

# Address of the Supervisor's ingress proxy. With ingress, every legitimate
# request comes from here; anything else on the internal network is rejected.
SUPERVISOR_INGRESS_IP = "172.30.32.2"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int = 20 * 1024 * 1024
    statistic_ids: dict[str, str] = field(default_factory=dict)
    ha_ws_url: str = "ws://supervisor/core/websocket"
    ha_token: str | None = None
    allowed_clients: frozenset[str] = frozenset({SUPERVISOR_INGRESS_IP})
    timezone: str = "Europe/Berlin"
    pdf_timeout_seconds: float = 60.0
    pdf_memory_limit_mb: int = 512

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dashboard.db"

    @property
    def documents_dir(self) -> Path:
        return self.data_dir / "documents"


def load_settings() -> Settings:
    """Build settings for the running app.

    In the HA app the Supervisor provides SUPERVISOR_TOKEN and TZ and writes the
    user's options to /data/options.json. For local development set DEV_MODE=1,
    HA_WS_URL (e.g. ws://192.168.178.50:8123/api/websocket) and HA_TOKEN
    (a long-lived access token).
    """
    dev_mode = os.environ.get("DEV_MODE") == "1"
    data_dir = Path(os.environ.get("DATA_DIR", "./data" if dev_mode else "/data"))

    options: dict = {}
    options_file = data_dir / "options.json"
    if options_file.exists():
        options = json.loads(options_file.read_text(encoding="utf-8"))

    statistic_ids = {
        medium: options[f"{medium}_statistic_id"].strip()
        for medium in MEDIA
        if options.get(f"{medium}_statistic_id", "").strip()
    }

    return Settings(
        data_dir=data_dir,
        max_upload_bytes=int(options.get("max_upload_mb", 20)) * 1024 * 1024,
        statistic_ids=statistic_ids,
        ha_ws_url=os.environ.get("HA_WS_URL", "ws://supervisor/core/websocket"),
        ha_token=os.environ.get("HA_TOKEN") or os.environ.get("SUPERVISOR_TOKEN"),
        allowed_clients=frozenset() if dev_mode else frozenset({SUPERVISOR_INGRESS_IP}),
        timezone=os.environ.get("TZ", "Europe/Berlin"),
    )
