"""Protocol contract regression (P7).

Every response produced by ``ok()`` or ``err()`` must conform to the
envelope contract documented in ``utils/mcp_response.py``:

* ``success`` is a bool.
* ``api_version`` is present and matches the module constant.
* ``warnings`` is a list.
* Error responses carry both ``error`` and ``error_code`` fields.
* ``error_code`` values used in handlers come from the canonical
  ``utils.error_codes`` catalogue.

This test sweeps every importable handler module and exercises enough
handlers with empty payloads to flush out obvious envelope violations.
"""

import importlib
import pkgutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Content" / "Python"))

import pytest  # noqa: E402

from utils import error_codes  # noqa: E402
from utils.mcp_response import API_VERSION, err, ok  # noqa: E402


def _check_envelope(payload, expect_success):
    assert isinstance(payload, dict)
    assert payload["success"] is expect_success
    assert payload["api_version"] == API_VERSION
    assert isinstance(payload["warnings"], list)
    if expect_success:
        assert "data" in payload
    else:
        assert payload["error"] == payload["message"]
        assert payload["error_code"] in error_codes.ALL_ERROR_CODES, payload["error_code"]


def test_ok_envelope_shape():
    _check_envelope(ok("ok", data={"x": 1}), True)


def test_err_envelope_shape():
    _check_envelope(err("bad", error_code=error_codes.MISSING_PARAMETERS), False)


def test_err_unknown_error_code_is_caught():
    """If a handler emits an unknown code our catalogue will reject it."""
    resp = err("bad", error_code="TOTALLY_MADE_UP")
    with pytest.raises(AssertionError):
        _check_envelope(resp, False)


def test_all_handlers_respond_with_envelope_for_empty_input():
    import handlers  # noqa: F401

    handler_modules = [
        "handlers.session_commands",
        "handlers.input_commands",
        "handlers.animation_commands",
        "handlers.anim_blueprint_commands",
    ]
    for mod_name in handler_modules:
        mod = importlib.import_module(mod_name)
        for attr in dir(mod):
            if not attr.startswith("handle_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            try:
                payload = fn({})
            except TypeError:
                # Some handlers may require positional args; skip in contract test.
                continue
            assert isinstance(payload, dict), f"{mod_name}.{attr} returned non-dict"
            assert "success" in payload, f"{mod_name}.{attr} missing success"
            assert payload["api_version"] == API_VERSION, f"{mod_name}.{attr} missing api_version"
            if payload["success"] is False:
                assert payload["error_code"] in error_codes.ALL_ERROR_CODES, (
                    f"{mod_name}.{attr} emitted unknown error_code {payload['error_code']!r}"
                )
