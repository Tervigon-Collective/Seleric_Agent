"""Secret resolution interface.

V1 reads secrets from environment variables / `.env` only.

Deployed environments should resolve the same env names from Azure Key Vault
using managed identity. Recommended secret names:

- `seleric-azure-openai-api-key` -> AZURE_OPENAI_API_KEY
- `seleric-langsmith-api-key` -> LANGSMITH_API_KEY

Do not implement a production Key Vault client in this prototype; keep the
boundary explicit so adapters never hardcode credentials.
"""

from __future__ import annotations

from .settings import Settings

KEY_VAULT_SECRET_NAMES = {
    "AZURE_OPENAI_API_KEY": "seleric-azure-openai-api-key",
    "LANGSMITH_API_KEY": "seleric-langsmith-api-key",
}


def resolve_secret(env_name: str, settings: Settings, current_value: str) -> str:
    """Return the in-process secret.

    If `settings.azure_key_vault_url` is set, a future adapter would fetch
    `KEY_VAULT_SECRET_NAMES[env_name]`. V1 never calls the network here.
    """
    if current_value:
        return current_value
    if settings.azure_key_vault_url:
        raise RuntimeError(
            f"Secret {env_name} is empty. In deployed environments resolve "
            f"{KEY_VAULT_SECRET_NAMES.get(env_name, env_name)} from Azure Key Vault "
            f"at {settings.azure_key_vault_url} via managed identity."
        )
    return current_value
