"""The runtime dependencies the video generation tool needs, and the heavy ones it must not pull in."""

from __future__ import annotations

import importlib
import importlib.util

import pytest


@pytest.mark.parametrize("module", ["google.genai", "python_multipart"])
def test_required_modules_import(module: str) -> None:
    importlib.import_module(module)


@pytest.mark.parametrize("module", ["torch", "transformers"])
def test_google_genai_local_tokenizer_extra_is_not_installed(module: str) -> None:
    # The `local-tokenizer` extra of google-genai drags in torch; the image size guard depends on it staying out.
    assert importlib.util.find_spec(module) is None
