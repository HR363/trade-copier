"""
Configuration loader for the Trade Copier.
Reads and validates accounts.json.
"""

import json
import os
from typing import Tuple, List
from src.models import AccountConfig, LotMode


class ConfigError(Exception):
    pass


def load_config(config_path: str) -> Tuple[AccountConfig, List[AccountConfig], dict]:
    """
    Load and validate the accounts.json configuration.

    Returns:
        (master_config, slave_configs, settings)
    """
    if not os.path.exists(config_path):
        raise ConfigError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        try:
            raw = json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in config file: {e}")

    # --- Parse master ---
    if "master" not in raw:
        raise ConfigError("Config must have a 'master' section.")

    master_raw = raw["master"]
    _validate_account_fields(master_raw, "master")
    master = AccountConfig(
        name=master_raw.get("name", "Master"),
        login=int(master_raw["login"]),
        password=str(master_raw["password"]),
        server=master_raw["server"],
        terminal_path=master_raw["terminal_path"],
        comment=master_raw.get("comment", ""),
    )

    # --- Parse slaves ---
    if "slaves" not in raw or not isinstance(raw["slaves"], list):
        raise ConfigError("Config must have a 'slaves' list.")

    slaves: List[AccountConfig] = []
    for i, s in enumerate(raw["slaves"]):
        label = f"slaves[{i}]"
        _validate_account_fields(s, label)

        lot_mode_str = s.get("lot_mode", "multiplier").lower()
        try:
            lot_mode = LotMode(lot_mode_str)
        except ValueError:
            raise ConfigError(
                f"{label}: lot_mode must be 'multiplier' or 'fixed', got '{lot_mode_str}'"
            )

        lot_value = float(s.get("lot_value", 1.0))
        if lot_value <= 0:
            raise ConfigError(f"{label}: lot_value must be > 0")

        slaves.append(AccountConfig(
            name=s.get("name", f"Slave {i + 1}"),
            login=int(s["login"]),
            password=str(s["password"]),
            server=s["server"],
            terminal_path=s["terminal_path"],
            enabled=bool(s.get("enabled", True)),
            lot_mode=lot_mode,
            lot_value=lot_value,
            comment=s.get("comment", ""),
        ))

    enabled_slaves = [s for s in slaves if s.enabled]
    if not enabled_slaves:
        raise ConfigError("No enabled slave accounts found in config.")

    # --- Parse settings ---
    settings = {
        "poll_interval_ms": 500,
        "copy_stop_loss": True,
        "copy_take_profit": True,
        "copy_pending_orders": False,
        "max_retries": 3,
        "retry_delay_ms": 200,
        "log_level": "INFO",
    }
    settings.update(raw.get("settings", {}))

    return master, slaves, settings


def _validate_account_fields(data: dict, label: str):
    required = ["login", "password", "server", "terminal_path"]
    for field in required:
        if field not in data:
            raise ConfigError(f"'{label}' is missing required field: '{field}'")
