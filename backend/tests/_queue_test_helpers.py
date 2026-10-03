"""Connections for isolated queue tests using the configured test services."""
from urllib.parse import urlsplit, urlunsplit

from app.core.config import settings


def redis_test_url():
    """Use a separate Redis database while retaining the configured endpoint."""
    return urlunsplit(urlsplit(settings.redis_url)._replace(path='/14'))
