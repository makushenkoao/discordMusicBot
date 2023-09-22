import discord
from discord import Intents
from discord.ext import commands

from src.cogs import *


class Bot(commands.Bot):
    def __init__(self, token: str):
        super().__init__(
            command_prefix=commands.when_mentioned_or(),
            activity=discord.Game(name="/play"),
            intents=Intents.all()
        )

        self.run(token)

    async def on_ready(self) -> None:
        await self.wait_until_ready()

        await self.add_cog(MusicCog(self))

        await self.tree.sync()
