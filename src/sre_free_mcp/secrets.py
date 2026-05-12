"""Secret Manager helper.

A thin wrapper around ``google-cloud-secret-manager`` that returns the
latest version of a secret as a string. Kept in its own module so unit
tests can monkeypatch it without pulling in the GCP client.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def get_secret(secret_name: str, *, project: str, version: str = "latest") -> str:
    """Return the secret value as a UTF-8 string.

    Raises whatever the underlying Secret Manager client raises (no
    catch-all) so authentication errors / not-found errors surface at
    the call site.
    """
    from google.cloud import secretmanager  # lazy

    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_name}/versions/{version}"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


__all__ = ["get_secret"]
