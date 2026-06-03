"""Tests for backend.exceptions hierarchy."""
from backend.exceptions import (
    HygieError, MediaServerUnreachable, ArrClientError,
    DeletionFailed, ConfigurationError,
)


def test_all_subclasses_inherit_hygie_error():
    for cls in (MediaServerUnreachable, ArrClientError, DeletionFailed, ConfigurationError):
        assert issubclass(cls, HygieError), f"{cls} should inherit from HygieError"


def test_hygie_error_inherits_exception():
    assert issubclass(HygieError, Exception)


def test_catch_specific_as_base():
    try:
        raise MediaServerUnreachable("emby down")
    except HygieError as e:
        assert "emby down" in str(e)


def test_exception_chaining():
    original = ConnectionError("timeout")
    try:
        raise ArrClientError("radarr failed") from original
    except ArrClientError as e:
        assert e.__cause__ is original
