import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from db.engine import async_session
from db.models import GuildConfig
from utils.permissions import get_or_create_guild_config


class ConfigCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    config_group = app_commands.Group(name="config", description="Server configuration for the event bot")

    @config_group.command(name="add_admin_role", description="Allow a role to accept/decline signups and manage parties")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def add_admin_role(self, interaction: discord.Interaction, role: discord.Role):
        async with async_session() as session:
            result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id))
            cfg = result.scalar_one_or_none()
            if cfg is None:
                cfg = GuildConfig(guild_id=interaction.guild_id, admin_role_ids=str(role.id))
                session.add(cfg)
            else:
                ids = set(cfg.admin_role_id_list())
                ids.add(role.id)
                cfg.admin_role_ids = ",".join(str(i) for i in ids)
            await session.commit()
        await interaction.response.send_message(f"✅ {role.mention} can now manage event signups and parties.", ephemeral=True)

    @config_group.command(name="remove_admin_role", description="Revoke event-management permission from a role")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_admin_role(self, interaction: discord.Interaction, role: discord.Role):
        async with async_session() as session:
            result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id))
            cfg = result.scalar_one_or_none()
            if cfg is None:
                await interaction.response.send_message("No admin roles configured yet.", ephemeral=True)
                return
            ids = set(cfg.admin_role_id_list())
            ids.discard(role.id)
            cfg.admin_role_ids = ",".join(str(i) for i in ids)
            await session.commit()
        await interaction.response.send_message(f"Removed {role.mention} from event managers.", ephemeral=True)

    @config_group.command(name="set_announcement_channel", description="Default channel for event signup posts")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_announcement_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        async with async_session() as session:
            result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == interaction.guild_id))
            cfg = result.scalar_one_or_none()
            if cfg is None:
                cfg = GuildConfig(guild_id=interaction.guild_id, announcement_channel_id=channel.id)
                session.add(cfg)
            else:
                cfg.announcement_channel_id = channel.id
            await session.commit()
        await interaction.response.send_message(f"✅ Default announcement channel set to {channel.mention}.", ephemeral=True)

    @config_group.command(name="show", description="Show current event-bot configuration")
    async def show(self, interaction: discord.Interaction):
        cfg = await get_or_create_guild_config(interaction.guild_id)
        role_mentions = [f"<@&{rid}>" for rid in cfg.admin_role_id_list()]
        chan = f"<#{cfg.announcement_channel_id}>" if cfg.announcement_channel_id else "Not set"
        embed = discord.Embed(title="Event Bot Config", color=discord.Color.blurple())
        embed.add_field(name="Admin roles", value=", ".join(role_mentions) or "None set (only Manage Server + creator can approve)", inline=False)
        embed.add_field(name="Announcement channel", value=chan, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ConfigCog(bot))
