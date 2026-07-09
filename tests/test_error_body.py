"""Goal B1d — tolerance of BOTH gateway error-body shapes (sdk-python).

The gateway error contract moved from a flat ``{"error": "<msg>"}`` string to a
structured ``{"error": {"code", "message", "request_id"}}`` object (B1a+). The
SDK must surface a real string message either way — never a dict rendered as the
message, never empty.

pytest-compatible; also runnable standalone (`py tests/test_error_body.py`)
because this repo has no test runner installed by default.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from palveron import _parse_error_body, PalveronError, PalveronValidationError  # noqa: E402


def test_new_object_shape():
    p = _parse_error_body(
        {"error": {"code": "validation_error", "message": "Prompt is required", "request_id": "req-1"}}
    )
    assert p["message"] == "Prompt is required"
    assert p["code"] == "validation_error"
    assert p["request_id"] == "req-1"


def test_legacy_flat_string_shape():
    p = _parse_error_body({"error": "Old flat message"})
    assert p["message"] == "Old flat message"
    assert p["code"] is None
    assert p["request_id"] is None


def test_message_is_always_str_never_dict():
    p = _parse_error_body({"error": {"code": "forbidden", "message": "Nope"}})
    assert isinstance(p["message"], str)
    # The whole point: the object must never leak through as the message.
    assert p["message"] == "Nope"
    assert "{" not in p["message"]


def test_top_level_message_fallback():
    p = _parse_error_body({"message": "Bare message"})
    assert p["message"] == "Bare message"


def test_legacy_field_preserved():
    p = _parse_error_body({"error": "bad", "field": "prompt"})
    assert p["field"] == "prompt"


def test_defensive_inputs_never_raise():
    for inp in (None, 42, "a string", [], True, {"error": []}, {"error": {"code": 123, "message": None}}):
        p = _parse_error_body(inp)
        assert p["message"] is None or isinstance(p["message"], str)
        assert p["code"] is None or isinstance(p["code"], str)


def test_server_code_additive_field_on_error():
    e = PalveronError("boom", code="CLIENT_ERROR", status_code=409, server_code="conflict")
    assert e.server_code == "conflict"
    assert e.code == "CLIENT_ERROR"  # SDK category is untouched
    # Backward-compatible default when omitted.
    assert PalveronError("x").server_code is None


def test_validation_error_still_constructs():
    e = PalveronValidationError("Prompt is required", "prompt", "req-9")
    assert str(e) == "Prompt is required"
    assert e.field == "prompt"
    assert e.code == "VALIDATION_ERROR"


if __name__ == "__main__":
    _tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in _tests:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"all {len(_tests)} python error-body tests passed")
