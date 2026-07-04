"""Test-runner settings (pytest; PADR-0002).

Real migrations against real PostgreSQL — deliberately no --nomigrations
anywhere: the integrity layer lives in migrations and must exist in test DBs.
"""

from dev_project.settings.base import *  # noqa: F403

# Fast, insecure hashing is fine for test fixtures.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
