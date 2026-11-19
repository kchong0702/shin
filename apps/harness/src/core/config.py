import os
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self, override

from dotenv import load_dotenv
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

_ROOT_PATH = Path(__file__).resolve().parents[2]
_DEFAULT_TOML = _ROOT_PATH / "config.toml"
_DEFAULT_ENV_FILE = _ROOT_PATH / ".env"

load_dotenv(_DEFAULT_ENV_FILE, override=False)

Family = Literal["claude", "glm", "openai"]
"""A model family, which picks the provider its models run on"""


class AppSettings(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, use_attribute_docstrings=True
    )

    name: str = "harness"
    """Application name"""

    version: str = "0.1.0"
    """Application's version"""

    route_prefix: str = "/zumi"
    """FastAPI router prefix"""

    host: str = "127.0.0.1"
    """Host address to bind the server to"""

    port: int = Field(default=8300, gt=0)
    """Port to listen on"""

    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    """Uvicorn log level"""


class AgentSettings(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, use_attribute_docstrings=True
    )

    name: str
    """Agent name, such as sothoth"""

    path: Path
    """The agent's profile.yaml, relative to the app folder; its folder holds
    system.md and skills/"""

    family: Family
    """Model family the model belongs to, such as openai, which picks its provider"""

    model: str
    """Exact model the agent runs on, such as gpt-6-luna; one [models] offers"""

    @field_validator("path")
    @classmethod
    def _from_app_folder(cls, path: Path) -> Path:
        """Reads a relative path from the app folder"""
        return path if path.is_absolute() else _ROOT_PATH / path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZUMI_HARNESS_",
        env_nested_delimiter="__",
        env_file=str(_DEFAULT_ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        case_sensitive=False,
        frozen=True,
        validate_default=True,
    )

    app: AppSettings = Field(default_factory=AppSettings)
    """Application and uvicorn server settings"""

    models: dict[Family, tuple[str, ...]] = {}
    """The models agents can run on, by family, read from config.toml's [models]"""

    agents: tuple[AgentSettings, ...] = ()
    """Every agent the app builds, read from config.toml's [[agents]] tables"""

    reasoning_effort: Literal["low", "medium", "high"] = "low"
    """How hard claude and openai models reason before answering"""

    recursion_limit: int = Field(default=100, gt=0)
    """Most graph steps one run may take before it fails; each model call and each
    tool call is a step"""

    gateway_url: HttpUrl | None = Field(
        default=None, validation_alias=AliasChoices("AIGW_BASE_URL", "gateway_url")
    )
    """OpenAI-compatible gateway that serves the glm model family"""

    gateway_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AIGW_API_KEY", "gateway_key")
    )
    """Key for the model gateway"""

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Reads init values, then the environment, then config.toml"""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(
                settings_cls,
                toml_file=os.environ.get("ZUMI_HARNESS_CONFIG", _DEFAULT_TOML),
            ),
            file_secret_settings,
        )

    @model_validator(mode="after")
    def _agents_run_offered_models(self) -> Self:
        """Refuses an agent whose model [models] does not offer"""
        for agent in self.agents:
            if agent.model not in self.models.get(agent.family, ()):
                raise ValueError(
                    f"{agent.name} runs {agent.family} {agent.model}, which [models] does not offer"
                )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The settings, read once per process"""
    return Settings()
