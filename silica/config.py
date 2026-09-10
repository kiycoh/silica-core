# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Silica configuration: the root, the converters, the optional embeddings.

Read from the environment first, then `~/.silica/.env`, then the defaults.
`SILICA_HOME` moves that whole runtime directory, and like `SILICA_VAULT` it
is read from the shell alone — a file cannot say where it itself lives.
`SILICA_VAULT` is honoured only when exported in the shell: in the user
file it is ignored with a warning, because Silica serves the folder it is
started in and a pinned root belongs to the invocation, not to a file.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

from silica import SHELL_ENV, VAULT_PINNED  # noqa: F401 — re-exported


def _silica_home() -> Path:
    """`~/.silica` unless SILICA_HOME overrides it: the runtime directory that
    holds `.env`, `index/`, `tmp/` and `inbox/`.

    Read from the real environment only, never from a .env file — the variable
    that says where the config lives cannot itself live in the config, the same
    rule SILICA_VAULT already follows. What it buys is a second root on one
    machine: running this distribution against a clean home while another tree
    keeps its own `~/.silica`, so neither inherits the other's model, keys or
    index. Renaming the directory outright would have done none of that and
    invalidated every installed user's index.
    """
    override = os.environ.get("SILICA_HOME", "").strip()
    return Path(os.path.expanduser(override)) if override else Path.home() / ".silica"


SILICA_HOME = _silica_home()
USER_ENV = SILICA_HOME / ".env"


def load_user_env(path: Path) -> None:
    """Layer `path` under the shell: an exported key still wins."""
    import silica as _pkg  # SHELL_ENV read at call time: tests replace it

    for key, value in (dotenv_values(path) if path.exists() else {}).items():
        if value is None:
            continue
        if key == "SILICA_VAULT":
            if not os.environ.get("SILICA_VAULT", "").strip():
                logging.getLogger(__name__).warning(
                    "SILICA_VAULT in %s is ignored: silica serves the folder it is "
                    "started in. Delete the line, or export SILICA_VAULT to pin a root.", path)
            continue
        if key in _pkg.SHELL_ENV and key in os.environ:
            continue
        os.environ[key] = value


load_user_env(USER_ENV)

_TRUE_WORDS = ("true", "1", "t")


def env_flag(key: str, default: bool) -> bool:
    return os.getenv(key, "true" if default else "false").lower() in _TRUE_WORDS


def _env(key: str, default: str = "", *aliases: str):
    return field(default_factory=lambda: next((os.getenv(k) for k in (key, *aliases) if os.getenv(k)), default))


@dataclass
class SilicaConfig:
    """Runtime configuration singleton."""

    # the root: the folder silica runs in unless SILICA_VAULT is exported or --vault given
    vault_path: str = _env("SILICA_VAULT")
    # tokenizer language for the index ("auto" detects per text)
    lang: str = _env("SILICA_LANG", "auto", "SILICA_COOCCURRENCE_LANG")
    debug_logging: bool = field(default_factory=lambda: env_flag("SILICA_VERBOSE", False))

    # converters
    pdf_provider: str = _env("SILICA_PDF_PROVIDER", "pdfium")
    pdf_ocr_lang: str = _env("SILICA_PDF_OCR_LANG", "en,it,fr,de,es")
    inbox_dir: str = _env("SILICA_INBOX_DIR", "Inbox")
    stt_provider: str = _env("SILICA_STT_PROVIDER", "endpoint", "SILICA_ASR_PROVIDER")
    stt_base_url: str = _env("SILICA_STT_BASE_URL", "http://localhost:1236/v1", "SILICA_ASR_BASE_URL")
    stt_model: str = _env("SILICA_STT_MODEL", "whisper-1", "SILICA_ASR_MODEL")
    stt_lang: str = _env("SILICA_STT_LANG", "auto", "SILICA_ASR_LANG")
    stt_api_key: str = _env("SILICA_STT_API_KEY", "lm-studio")
    stt_whispercpp_bin: str = _env("SILICA_STT_WHISPERCPP_BIN", "", "SILICA_ASR_WHISPERCPP_BIN")
    stt_whispercpp_model: str = _env("SILICA_STT_WHISPERCPP_MODEL", "", "SILICA_ASR_WHISPERCPP_MODEL")

    # optional embeddings extension (OpenAI-compatible /v1/embeddings); off until asked for
    embedding_base_url: str = _env("SILICA_EMBEDDING_BASE_URL")
    embedding_model: str = _env("SILICA_EMBEDDING_MODEL", "text-embedding-qwen3-embedding-4b")
    embedding_api_key: str = _env("SILICA_EMBEDDING_API_KEY", "lm-studio")
    # what the model wants in front of a document and of a query: nomic-embed-text
    # needs "search_document: " / "search_query: " (its card: the prefix must be
    # there), potion and qwen3 want neither, so both are empty until named
    embedding_doc_prefix: str = _env("SILICA_EMBEDDING_DOC_PREFIX")
    embedding_query_prefix: str = _env("SILICA_EMBEDDING_QUERY_PREFIX")

    # the optional REPL's model: "provider/model" or a bare id served by SILICA_PROVIDER_BASE_URL
    model: str = _env("SILICA_MODEL")
    provider_base_url: str = _env("SILICA_PROVIDER_BASE_URL")
    provider_api_key: str = _env("SILICA_PROVIDER_API_KEY")

    # Obsidian bridge
    ws_port: int = field(default_factory=lambda: int(os.getenv("SILICA_WS_PORT", "0")))
    ws_token: str = _env("SILICA_WS_TOKEN")


CONFIG = SilicaConfig()
