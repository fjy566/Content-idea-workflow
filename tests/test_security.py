import pytest

from app.security import validate_public_url


def test_security_rejects_private_literal_addresses():
    with pytest.raises(ValueError):
        validate_public_url("http://127.0.0.1/feed.xml")
    with pytest.raises(ValueError):
        validate_public_url("http://169.254.169.254/latest/meta-data")


def test_security_rejects_non_http_schemes():
    with pytest.raises(ValueError):
        validate_public_url("file:///etc/passwd")

