API_VERSION = "2026-04-19"


def _extra_fields(extra, reserved_keys):
    return {
        key: value
        for key, value in extra.items()
        if key not in reserved_keys and value is not None
    }


def ok(message="", data=None, warnings=None, **extra):
    payload = {
        "success": True,
        "message": message,
        "data": {} if data is None else data,
        "warnings": [] if warnings is None else warnings,
        "api_version": API_VERSION,
    }
    payload.update(_extra_fields(extra, payload))
    return payload


def err(message, error_code="UNKNOWN_ERROR", warnings=None, **extra):
    payload = {
        "success": False,
        "message": message,
        "error": message,
        "error_code": error_code,
        "warnings": [] if warnings is None else warnings,
        "api_version": API_VERSION,
    }
    payload.update(_extra_fields(extra, payload))
    return payload
