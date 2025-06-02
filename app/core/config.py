"""
Configuration settings for Elettra Backend with external configuration file support
"""

import os
import json
import yaml
from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, validator, root_validator, ValidationError
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

    # Performance
    request_timeout_seconds: int
    max_request_size_mb: int

    # Optional key (keep empty dict if missing)
    external_api_endpoints: Dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Helpers & config
    # ------------------------------------------------------------------ #
    @root_validator(pre=True)
    def _normalise_origins(cls, values):
        raw = values.get("allowed_origins")
        if isinstance(raw, str):
            values["allowed_origins"] = [o.strip() for o in raw.split(",") if o.strip()]
        return values

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "allow"  # allow future keys without code changes

    def get_database_url(self) -> str:
        """Get database URL"""
        return self.database_url

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
    except Exception as e:
        logger.error("Error reading configuration file: %s", e)
        return {}

def get_settings() -> Settings:
    """
    Locate the configuration file, load it and return a validated Settings
    object.  No in-code defaults exist, so any missing key raises a
    ValidationError and stops application startup.
    """
    # 1.  Find config path ----------------------------------------------------
    cfg_path = os.getenv("ELETTRA_CONFIG_FILE")
    if not cfg_path:
        for candidate in (
            "config/elettra-config.yaml",
            "config/elettra-config.yml",
            "config/elettra-config.json",
            "./elettra-config.yaml",
            "./elettra-config.yml",
            "./elettra-config.json",
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

    # 2.  Load + validate -----------------------------------------------------
    cfg_dict = load_config_from_file(cfg_path)
    try:
        return Settings(**cfg_dict)
    except ValidationError as err:
        logger.error("Invalid / incomplete configuration file '%s':\n%s", cfg_path, err)
        raise

# Global settings instance
_settings: Optional[Settings] = None

def get_cached_settings() -> Settings:
    """Get cached settings instance"""
    global _settings
    if _settings is None:
        _settings = get_settings()
    return _settings

def reload_settings():
    """Reload settings"""
    global _settings
    _settings = None
    _settings = get_settings()
    logger.info("Settings reloaded")

# --------------------------------------------------------------------------- #
# Helper used later to merge defaults back into the YAML file
# --------------------------------------------------------------------------- #
def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    """Safely dump `data` back to *path* in YAML format."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            # Preserve key order – nice to read
            yaml.safe_dump(data, fh, sort_keys=False)
        logger.info("📄 Added missing keys – configuration file updated at %s", path)
    except Exception as exc:  # pragma: no cover
        logger.error("Unable to write updated configuration file %s: %s", path, exc)

def _merge_defaults(original: Dict[str, Any], defaults: Dict[str, Any]) -> bool:
    """
    Add any key that is present in *defaults* but missing in *original*.

    Returns True if *original* was modified.
    """
    updated = False
    for key, value in defaults.items():
        if key not in original:
            original[key] = value
            updated = True
    return updated 