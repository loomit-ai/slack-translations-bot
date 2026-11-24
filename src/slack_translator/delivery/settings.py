"""CFG-01 to CFG-16 · Runtime configuration, read once at startup.

`Settings` reads nothing by itself: `from_environ` takes the mapping, so tests pass an explicit
dictionary (TS-08) and the process environment is read in one place only (`delivery/main.py`).
Empty values count as unset. Validation errors name keys and never echo values (SCR-01).
"""

import json
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self, cast
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, PydanticBaseSettingsSource, SettingsConfigDict

from slack_translator.domain.languages import (
    ConfiguredLanguage,
    LanguageCode,
    LanguageConfigError,
    parse_language_seed,
)

DEFAULT_LANGUAGE_SEED = "en:team,es:team,ru:customer,ko:customer,vi:customer,zh:customer"

_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{8,}$")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        hide_input_in_errors=True,
    )

    # CFG-01
    channel_allowlist: Annotated[tuple[str, ...], NoDecode]
    # CFG-02
    language_seed: Annotated[tuple[ConfiguredLanguage, ...], NoDecode] = parse_language_seed(
        DEFAULT_LANGUAGE_SEED
    )
    # CFG-03
    read_language: Annotated[LanguageCode, Field(validate_default=True)] = LanguageCode("en")
    # CFG-04: the minimum lead of the top language over the runner-up (IF-02), 0.5 to 1
    classifier_min_confidence: Annotated[float, Field(ge=0.5, le=1)] = 0.7
    # CFG-05
    classifier_timeout: Annotated[float, Field(ge=0.1, le=10)] = 3
    # CFG-06
    translator_timeout: Annotated[float, Field(ge=1, le=60)] = 20
    # CFG-07
    translator_base_url: str
    # CFG-08
    translator_model: Annotated[str, Field(min_length=1)]
    # CFG-09
    translator_api_key: SecretStr
    # CFG-10
    slack_bot_token: SecretStr
    # CFG-11
    slack_app_token: SecretStr
    # CFG-12
    slack_mode: Literal["socket", "http"] = "socket"
    # CFG-13
    app_env: Literal["development", "staging", "production"]
    # CFG-14
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # CFG-15
    port: Annotated[int, Field(ge=1024, le=65535)] = 8000
    # CFG-16
    translator_extra_body: Annotated[dict[str, Any], NoDecode] = Field(default_factory=dict)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)

    @classmethod
    def from_environ(cls, environ: Mapping[str, str]) -> Self:
        values = {key.lower(): value for key, value in environ.items() if value.strip() != ""}
        return cls.model_validate(values)

    @field_validator("channel_allowlist", mode="before")
    @classmethod
    def _parse_allowlist(cls, value: object) -> tuple[str, ...]:
        if not isinstance(value, str):
            raise ValueError("must be a comma-separated list of channel IDs")
        ids = [part.strip() for part in value.split(",")]
        if not ids or any(not _CHANNEL_ID.match(channel) for channel in ids):
            raise ValueError("must list channel IDs such as C0123ABCDE or G0123ABCDE")
        return tuple(dict.fromkeys(ids))

    @field_validator("language_seed", mode="before")
    @classmethod
    def _parse_seed(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            return parse_language_seed(value)
        except LanguageConfigError as error:
            raise ValueError(str(error)) from None

    @field_validator("read_language")
    @classmethod
    def _read_language_is_a_team_language(
        cls, value: LanguageCode, info: ValidationInfo
    ) -> LanguageCode:
        seed: tuple[ConfiguredLanguage, ...] | None = info.data.get("language_seed")
        if seed is not None and not any(
            lang.code == value and lang.role == "team" for lang in seed
        ):
            raise ValueError("must be a team language in LANGUAGE_SEED")
        return value

    @field_validator("translator_base_url")
    @classmethod
    def _translator_url_is_safe(cls, value: str) -> str:
        parts = urlsplit(value)
        local = parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS
        if not (parts.scheme == "https" or local) or not parts.hostname:
            raise ValueError("must be an https URL (http only for localhost)")
        if value.endswith("/"):
            raise ValueError("must not end with a slash")
        return value

    @field_validator("translator_extra_body", mode="before")
    @classmethod
    def _parse_extra_body(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        try:
            parsed = json.loads(value)
        except ValueError:
            raise ValueError("must be a JSON object") from None
        if not isinstance(parsed, dict):
            raise ValueError("must be a JSON object")
        return cast(dict[str, Any], parsed)

    @field_validator("translator_api_key")
    @classmethod
    def _api_key_is_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("slack_bot_token")
    @classmethod
    def _bot_token_prefix(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("xoxb-"):
            raise ValueError("must start with xoxb-")
        return value

    @field_validator("slack_app_token")
    @classmethod
    def _app_token_prefix(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("xapp-"):
            raise ValueError("must start with xapp-")
        return value

    @field_validator("slack_mode")
    @classmethod
    def _only_socket_mode_before_0_6(cls, value: str) -> str:
        if value != "socket":
            raise ValueError("only socket is accepted before milestone 0.6")
        return value

    @field_validator("log_level")
    @classmethod
    def _debug_only_in_development(cls, value: str, info: ValidationInfo) -> str:
        if value == "DEBUG" and info.data.get("app_env") != "development":
            raise ValueError("DEBUG is accepted only when APP_ENV is development")
        return value


def invalid_keys(error: ValidationError) -> list[str]:
    """The environment keys a validation error is about, in declaration order, without values."""
    keys: list[str] = []
    for item in error.errors(include_input=False, include_url=False, include_context=False):
        key = str(item["loc"][0]).upper() if item["loc"] else "SETTINGS"
        if key not in keys:
            keys.append(key)
    return keys
