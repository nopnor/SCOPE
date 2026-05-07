from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(slots=True)
class ProxySettings:
    proxy_on: bool = False
    http_proxy: str = os.getenv("HTTP_PROXY", "http://127.0.0.1:7890")
    https_proxy: str = os.getenv("HTTPS_PROXY", "http://127.0.0.1:7890")


@dataclass(slots=True)
class ControllerSettings:
    owner: str = os.getenv("SCOPE_CONTROLLER", "outer_agent")
    agent_family: str = os.getenv("SCOPE_CONTROLLER_AGENT", "codex")


@dataclass(slots=True)
class ImageGenSettings:
    provider: str = os.getenv("SCOPE_IMAGE_PROVIDER", "jdcloud_gemini")
    api_key: str = field(
        default_factory=lambda: (
            os.getenv("SCOPE_IMAGE_API_KEY")
            or os.getenv("JDCLOUD_API_KEY")
            or os.getenv("JDCLOUD_MODEL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
    )
    base_url: str = os.getenv("SCOPE_IMAGE_BASE_URL", "https://modelservice.jdcloud.com/v1")
    gen_model: str = os.getenv("SCOPE_IMAGE_MODEL", "Gemini 3-Pro-Image-Preview")
    edit_model: str = os.getenv("SCOPE_IMAGE_EDIT_MODEL", "Gemini 3-Pro-Image-Preview")

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and (self.gen_model or self.edit_model))


@dataclass(slots=True)
class JudgeSettings:
    provider: str = os.getenv("SCOPE_JUDGE_PROVIDER", "jdcloud_gemini")
    api_key: str = field(
        default_factory=lambda: (
            os.getenv("SCOPE_JUDGE_API_KEY")
            or os.getenv("JDCLOUD_API_KEY")
            or os.getenv("JDCLOUD_MODEL_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
    )
    base_url: str = os.getenv("SCOPE_JUDGE_BASE_URL", "https://modelservice.jdcloud.com/v1")
    model_name: str = os.getenv("SCOPE_JUDGE_MODEL", "Gemini 3-Pro-Preview")
    request_timeout: int = int(os.getenv("SCOPE_JUDGE_TIMEOUT", "180"))

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model_name)


@dataclass(slots=True)
class SearchSettings:
    provider: str = os.getenv("SCOPE_SEARCH_PROVIDER", "serper")
    serper_api_key: str = field(
        default_factory=lambda: (
            os.getenv("SCOPE_SERPER_API_KEY")
            or os.getenv("SERPER_API_KEY")
            or ""
        )
    )
    num_results: int = int(os.getenv("SCOPE_SEARCH_NUM_RESULTS", "5"))
    request_timeout: int = int(os.getenv("SCOPE_SEARCH_TIMEOUT", "20"))

    @property
    def configured(self) -> bool:
        return bool(self.provider == "serper" and self.serper_api_key)


@dataclass(slots=True)
class TempDirSettings:
    default: str = str(_REPO_ROOT / "temp")
    image_gen: str = str(_REPO_ROOT / "temp" / "image_gen")
    image_rag: str = str(_REPO_ROOT / "temp" / "image_rag")
    text_rag: str = str(_REPO_ROOT / "temp" / "text_rag")


@dataclass(slots=True)
class RuntimeSettings:
    controller: ControllerSettings = field(default_factory=ControllerSettings)
    image_gen: ImageGenSettings = field(default_factory=ImageGenSettings)
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    proxy: ProxySettings = field(default_factory=ProxySettings)
    temp_dir: TempDirSettings = field(default_factory=TempDirSettings)

    def setup_proxy(self) -> None:
        if self.proxy.proxy_on:
            os.environ["http_proxy"] = self.proxy.http_proxy
            os.environ["https_proxy"] = self.proxy.https_proxy
            os.environ["HTTP_PROXY"] = self.proxy.http_proxy
            os.environ["HTTPS_PROXY"] = self.proxy.https_proxy
            return
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
            os.environ.pop(key, None)

    def get_temp_dir(self, name: str = "default") -> Path:
        dir_map = {
            "default": self.temp_dir.default,
            "image_gen": self.temp_dir.image_gen,
            "image_rag": self.temp_dir.image_rag,
            "text_rag": self.temp_dir.text_rag,
        }
        path = Path(dir_map.get(name, self.temp_dir.default)).absolute()
        path.mkdir(parents=True, exist_ok=True)
        return path

    def describe(self) -> dict:
        return {
            "controller": {
                "owner": self.controller.owner,
                "agent_family": self.controller.agent_family,
            },
            "image_gen": {
                "provider": self.image_gen.provider,
                "base_url": self.image_gen.base_url,
                "gen_model": self.image_gen.gen_model,
                "edit_model": self.image_gen.edit_model,
                "configured": self.image_gen.configured,
            },
            "judge": {
                "provider": self.judge.provider,
                "base_url": self.judge.base_url,
                "model_name": self.judge.model_name,
                "configured": self.judge.configured,
            },
            "search": {
                "provider": self.search.provider,
                "configured": self.search.configured,
                "num_results": self.search.num_results,
            },
            "proxy_on": self.proxy.proxy_on,
            "temp_dir_root": self.temp_dir.default,
        }


@lru_cache(maxsize=1)
def get_runtime_settings() -> RuntimeSettings:
    settings = RuntimeSettings()
    settings.setup_proxy()
    return settings
