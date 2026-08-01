import os
import asyncio
import logging

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.engine import init_db

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("albion_bot")

TOKEN = os.getenv("DISCORD_TOKEN")
DEV_GUILD_ID = os.getenv("DEV_GUILD_ID")

INTENTS = discord.Intents.default()
INTENTS.members = True          # needed to resolve accepted players & check voice state
INTENTS.voice_states = True     # needed for the 15-min "already in voice" check
INTENTS.message_content = False # not needed, everything is slash commands

COGS = [
    "cogs.config",
    "cogs.presets",
    "cogs.events",
    "cogs.parties",
    "cogs.reminders",
    "cogs.manager",
]


class AlbionBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)

    async def setup_hook(self):
        await init_db()
        for cog in COGS:
            await self.load_extension(cog)
            logger.info(f"Loaded {cog}")

        if DEV_GUILD_ID:
            guild = discord.Object(id=int(DEV_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info(f"Synced commands to dev guild {DEV_GUILD_ID}")
        else:
            await self.tree.sync()
            logger.info("Synced commands globally (can take up to 1hr to propagate)")

    async def on_ready(self):
        logger.info(f"Logged in as {self.user} ({self.user.id})")


async def main():
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN not set. Copy .env.example to .env and fill it in.")
    bot = AlbionBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot shut down gracefully.")
