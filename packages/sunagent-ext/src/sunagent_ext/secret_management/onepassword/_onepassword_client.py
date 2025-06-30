import os
import logging
from typing import Optional

from onepassword.client import Client

from autogen_core import EVENT_LOGGER_NAME, TRACE_LOGGER_NAME

logger = logging.getLogger(EVENT_LOGGER_NAME)
trace_logger = logging.getLogger(TRACE_LOGGER_NAME)


class OnePasswordManager:
    def __init__(self, token:str, integration_name: str, integration_version: str, op_path: str) -> None:
        self._client = None
        self.token = token
        self.integration_name = integration_name
        self.integration_version = integration_version
        self.op_path = op_path
        self._cache: dict[str, str] = {}

    async def initialize(self) -> None:
        """
        Initialize the 1Password client with the service account token.
        """
        if not self._client:
            self._client = await Client.authenticate(
                auth=self.token, integration_name=self.integration_name, integration_version=self.integration_version
            )

    async def get_secret(
        self,
        item_title: Optional[str] = None,
        field_label: Optional[str] = "credential",
        secret_ref: Optional[str] = None,
    ) -> str:
        """
        Retrieve a secret from 1Password with flexible reference resolution.

        This method supports two modes of operation:
        1. By providing individual components (via parameters)
        2. By providing a complete secret reference string

        Args:
            item_title: Title of the item containing the secret.
                        Example: "GOOGLE_GEMINI_API_KEY"/"OPENAI_API_KEY".

            field_label: The field label in the 1Password item.
                        Examples: "username", "password", "credential". (It depends on the item type. For example, for a API Credential item, it can be "username" or "credential")
                        If None, it will use "credential" as the default vault name.

            secret_ref: Complete secret reference in op:// format as alternative to components.
                        Example: "op://DEVELOPMENT/GOOGLE_GEMINI_API_KEY/credential".
                        If provided, overrides the component-based resolution.

        Returns:
            The secret value as a string.

        Raises:
            ValueError: If required parameters are missing or env vars not set.
        """
        await self.initialize()

        # Resolve the complete secret reference
        if not secret_ref:
            secret_ref = f"op://{self.op_path}/{item_title}/{field_label}"

        # Fetch from 1Password
        secret = ""
        if self._client is not None:
            try:
                secret = await self._client.secrets.resolve(secret_ref)
            except Exception as e:
                logger.error(f"Error retrieving secrets from the given address: {str(e)}")
                raise e

        return secret
