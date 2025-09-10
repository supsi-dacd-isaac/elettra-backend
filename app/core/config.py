"""
Configuration settings for Elettra Backend with external configuration file support
(Nested YAML structure + Pydantic v2 alias paths)
"""

import os
import json
import yaml
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable

from pydantic_settings import BaseSettings
from pydantic import Field, ValidationError, model_validator, ConfigDict, AliasPath

import logging

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Settings – values come from a **nested** YAML file via AliasPath mappings
# --------------------------------------------------------------------------- #
class Settings(BaseSettings):
    """
    Settings mapped from a nested YAML via AliasPath.
    No in-code defaults for required fields—your YAML should provide them.
    """

    # Pydantic v2 config
    model_config = ConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="allow",  # allow future keys without code changes
    )

    # ---- app ----
    app_name: str = Field(..., validation_alias=AliasPath("app", "name"))
    app_version: str = Field(..., validation_alias=AliasPath("app", "version"))
    debug: bool = Field(..., validation_alias=AliasPath("app", "debug"))

    # ---- database ----
    database_url: str = Field(..., validation_alias=AliasPath("database", "url"))
    database_echo: bool = Field(..., validation_alias=AliasPath("database", "echo"))

    # ---- server ----
    host: str = Field(..., validation_alias=AliasPath("server", "host"))
    port: int = Field(..., validation_alias=AliasPath("server", "port"))
    reload: bool = Field(..., validation_alias=AliasPath("server", "reload"))

    # ---- auth / JWT ----
    secret_key: str = Field(..., validation_alias=AliasPath("auth", "secret_key"))
    algorithm: str = Field(..., validation_alias=AliasPath("auth", "algorithm"))
    access_token_expire_minutes: int = Field(
        ..., validation_alias=AliasPath("auth", "access_token_expire_minutes")
    )

    # ---- CORS ----
    allowed_origins: List[str] = Field(
        ..., validation_alias=AliasPath("cors", "origins")
    )
    # (If you later want to use cors.credentials/methods/headers, add fields + aliases.)

    # ---- logging ----
    log_level: str = Field(..., validation_alias=AliasPath("logging", "level"))
    log_format: str = Field(
        ..., validation_alias=AliasPath("logging", "format")
    )

    # ---- PVGIS ----
    pvgis_coerce_year: int = Field(..., validation_alias=AliasPath("pvgis", "coerce_year"))

    # ---- Elettra specifics ----
    max_route_length_km: float = Field(..., validation_alias=AliasPath("elettra", "max_route_length_km"))
    max_bus_capacity: int = Field(..., validation_alias=AliasPath("elettra", "max_bus_capacity"))
    battery_efficiency_factor: float = Field(..., validation_alias=AliasPath("elettra", "battery_efficiency_factor"))
    default_charging_power_kw: float = Field(..., validation_alias=AliasPath("elettra", "default_charging_power_kw"))

    # ---- Simulation ----
    max_concurrent_simulations: int = Field(..., validation_alias=AliasPath("simulation", "max_concurrent_simulations"))
    simulation_timeout_minutes: int = Field(..., validation_alias=AliasPath("simulation", "simulation_timeout_minutes"))

    # ---- File system ----
    temp_dir: str = Field(..., validation_alias=AliasPath("paths", "temp_dir"))
    upload_dir: str = Field(..., validation_alias=AliasPath("paths", "upload_dir"))
    elevation_profiles_path: str = Field(..., validation_alias=AliasPath("paths", "elevation_profiles_path"))

    # ---- Performance ----
    request_timeout_seconds: int = Field(..., validation_alias=AliasPath("performance", "request_timeout_seconds"))
    max_request_size_mb: int = Field(..., validation_alias=AliasPath("performance", "max_request_size_mb"))

    # Optional key (keep empty dict if missing)
    external_api_endpoints: Dict[str, str] = Field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # Normalisers (Pydantic v2 style)
    # ------------------------------------------------------------------ #
    @model_validator(mode="before")
    @classmethod
    def _normalise_origins(cls, values: Dict[str, Any]):  # type: ignore[override]
        """
        Accept both:
          - top-level "allowed_origins": "a,b,c"
          - nested "cors": { "origins": "a,b,c" }
        and normalize to list[str].
        """
        # Case 1: top-level flat value (rare, but allow it)
        raw_flat = values.get("allowed_origins")
        if isinstance(raw_flat, str):
            values["allowed_origins"] = [o.strip() for o in raw_flat.split(",") if o.strip()]

        # Case 2: nested under cors.origins
        cors = values.get("cors")
        if isinstance(cors, dict):
            raw_nested = cors.get("origins")
            if isinstance(raw_nested, str):
                cors["origins"] = [o.strip() for o in raw_nested.split(",") if o.strip()]
                values["cors"] = cors

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


def _set_in_nested(d: Dict[str, Any], path: List[str], value: Any) -> None:
    """Set a nested key path in a dict, creating intermediate dicts."""
    cur = d
    for key in path[:-1]:
        if key not in cur or not isinstance(cur[key], dict):
            cur[key] = {}
        cur = cur[key]
    cur[path[-1]] = value


def get_settings() -> Settings:
    """
    Locate the configuration file, load it and return a validated Settings
    object. Missing keys raise a ValidationError and stop application startup.
    """
    cfg_path = os.getenv("ELETTRA_CONFIG_FILE")
    if not cfg_path:
        for candidate in (
            "config/elettra-config.yaml",
            "config/elettra-config.yml",
            "config/elettra-config.json",
            "config/elettra-config.docker.yaml",  # docker image default
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

    # Explicit environment variable overrides to nested paths
    # (kept minimal for container flexibility)
    def _as_bool(s: str) -> bool:
        return s.strip().lower() in ("1", "true", "yes", "on")

    def _as_csv_list(s: str) -> List[str]:
        return [o.strip() for o in s.split(",") if o.strip()]

    override_env_map: Dict[str, tuple[List[str], Callable[[str], Any]]] = {
        "DATABASE_URL": (["database", "url"], str),
        "APP_LOG_LEVEL": (["logging", "level"], str),
        "APP_DEBUG": (["app", "debug"], _as_bool),
        "APP_ALLOWED_ORIGINS": (["cors", "origins"], _as_csv_list),
        "APP_SECRET_KEY": (["auth", "secret_key"], str),
    }

    for env_key, (path_keys, caster) in override_env_map.items():
        raw = os.environ.get(env_key)
        if raw is not None and raw.strip():
            try:
                val = caster(raw)
                _set_in_nested(cfg_dict, path_keys, val)
                logger.info("Overrode config '%s' from ENV '%s'", ".".join(path_keys), env_key)
            except Exception as exc:
                logger.error("Failed to apply ENV override %s: %s", env_key, exc)

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
