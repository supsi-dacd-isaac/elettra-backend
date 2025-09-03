"""
Configuration settings for Elettra Backend with external configuration file support
"""

import os
import json
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, ValidationError, model_validator, ConfigDict
from typing import List, Optional, Dict, Any
import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Settings – *no* in-code defaults. Every value must come from the YAML file
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    # Core
    app_name: str
    app_version: str
    debug: bool

    # Database
    database_url: str
    database_echo: bool

    # API / server
    host: str
    port: int
    reload: bool

    # Security / JWT
    secret_key: str
    algorithm: str
    access_token_expire_minutes: int

    # CORS
    allowed_origins: List[str]

    # Elettra specifics
    max_route_length_km: float
    max_bus_capacity: int
    battery_efficiency_factor: float
    default_charging_power_kw: float

    # Simulation
    max_concurrent_simulations: int
    simulation_timeout_minutes: int

    # Logging
    log_level: str
    log_format: str

    # File system
    temp_dir: str
    upload_dir: str
    elevation_profiles_path: str

    # Performance
    request_timeout_seconds: int
    max_request_size_mb: int

    # Optional key (keep empty dict if missing)
    external_api_endpoints: Dict[str, str] = Field(default_factory=dict)

    # Pydantic v2 config
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="allow"  # allow future keys without code changes
    )

    # ------------------------------------------------------------------ #
    # Normalisers (Pydantic v2 style)
    # ------------------------------------------------------------------ #
    @model_validator(mode="before")
    @classmethod
    def _normalise_origins(cls, values: Dict[str, Any]):  # type: ignore[override]
        raw = values.get("allowed_origins")
        if isinstance(raw, str):
            values["allowed_origins"] = [o.strip() for o in raw.split(",") if o.strip()]
        return values

    def get_database_url(self) -> str:  # Convenience wrapper
        return self.database_url

# --------------------------------------------------------------------------- #
# Loader helpers
# --------------------------------------------------------------------------- #

def load_config_from_file(config_path: str) -> Dict[str, Any]:
    """Load configuration from external file (JSON or YAML)"""
    if not os.path.exists(config_path):
        logger.warning(f"Configuration file not found: {config_path}")
        return {}

    try:
        with open(config_path, "r", encoding="utf-8") as file:
            file_extension = Path(config_path).suffix.lower()

            if file_extension in [".yml", ".yaml"]:
                config_data = yaml.safe_load(file) or {}
                logger.info("Loaded YAML configuration from: %s", config_path)
            elif file_extension == ".json":
                config_data = json.load(file) or {}
                logger.info("Loaded JSON configuration from: %s", config_path)
            else:
                logger.error("Unsupported configuration file format: %s", file_extension)
                return {}
        return config_data
    except Exception as e:  # pragma: no cover
        logger.error("Error reading configuration file: %s", e)
        return {}

def get_settings() -> Settings:
    """
    Locate the configuration file, load it and return a validated Settings
    object.  No in-code defaults exist, so any missing key raises a
    ValidationError and stops application startup.
    """
    cfg_path = os.getenv("ELETTRA_CONFIG_FILE")
    if not cfg_path:
        for candidate in (
            "config/elettra-config.yaml",
            "config/elettra-config.yml",
            "config/elettra-config.json",
            "config/elettra-config.docker.yaml",  # added candidate so docker image works without env var
            "./elettra-config.yaml",
            "./elettra-config.yml",
            "./elettra-config.json",
            "./elettra-config.docker.yaml",
        ):
            if Path(candidate).exists():
                cfg_path = candidate
                logger.info("Using configuration file: %s", cfg_path)
                break

    if not cfg_path:
        raise FileNotFoundError(
            "Configuration file not found. "
            "Set ELETTRA_CONFIG_FILE or place it at 'config/elettra-config.yaml'."
        )

    cfg_dict = load_config_from_file(cfg_path)

    # Explicit environment variable overrides (minimal set for container flexibility)
    override_env_map = {
        'DATABASE_URL': 'database_url',
        'APP_LOG_LEVEL': 'log_level',
        'APP_DEBUG': 'debug',
        'APP_ALLOWED_ORIGINS': 'allowed_origins',  # comma separated
        'APP_SECRET_KEY': 'secret_key'
    }
    for env_key, target_key in override_env_map.items():
        if env_key in os.environ and os.environ[env_key].strip():
            val = os.environ[env_key].strip()
            if target_key == 'allowed_origins':
                cfg_dict[target_key] = [o.strip() for o in val.split(',') if o.strip()]
            elif target_key == 'debug':
                cfg_dict[target_key] = val.lower() in ('1', 'true', 'yes', 'on')
            else:
                cfg_dict[target_key] = val
            logger.info("Overrode config key '%s' from ENV '%s'", target_key, env_key)

    try:
        return Settings(**cfg_dict)
    except ValidationError as err:  # pragma: no cover
        logger.error("Invalid / incomplete configuration file '%s':\n%s", cfg_path, err)
        raise

# Global settings instance (simple cache)
_settings: Optional[Settings] = None

def get_cached_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings

def reload_settings():
    global _settings
    _settings = None
    _settings = get_settings()
    logger.info("Settings reloaded")

# --------------------------------------------------------------------------- #
# Helper used later to merge defaults back into the YAML file
# --------------------------------------------------------------------------- #

def _write_yaml(path: Path, data: Dict[str, Any]) -> None:  # pragma: no cover
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False)
        logger.info("📄 Added missing keys – configuration file updated at %s", path)
    except Exception as exc:
        logger.error("Unable to write updated configuration file %s: %s", path, exc)

def _merge_defaults(original: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
    updated = False
    for key, value in defaults.items():
        if key not in original:
            original[key] = value
            updated = True
    return updated
