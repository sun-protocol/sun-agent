import asyncio
import os
from sunagent_ext.secret_management.onepassword import OnePasswordManager

class Config:

    def __init__(self, integration_name, token, vault_name):
        self.vault_name = vault_name
        self.password_manager = OnePasswordManager(token, integration_name, "0.0.1", vault_name)


    async def initialize(self):
        await self.password_manager.initialize()

    async def get_env(self, key, dafault="",) -> str:
        env =  os.getenv(key)
        if env:
            return env
        try:
            return  await self.password_manager.get_secret(
                secret_ref=f"op://{self.vault_name}/{key}"
            )
        except Exception:
            return dafault


