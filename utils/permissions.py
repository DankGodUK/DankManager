import discord
from sqlalchemy import select

from db.engine import async_session
from db.models import GuildConfig, Event


async def get_or_create_guild_config(guild_id: int) -> GuildConfig:
    async with async_session() as session:
        result = await session.execute(select(GuildConfig).where(GuildConfig.guild_id == guild_id))
        cfg = result.scalar_one_or_none()
        if cfg is None:
            cfg = GuildConfig(guild_id=guild_id, admin_role_ids="")
            session.add(cfg)
            await session.commit()
            await session.refresh(cfg)
        return cfg


async def is_event_manager(member: discord.Member, event: Event) -> bool:
    """True if member is the event creator, has Manage Server/Guild permission,
    or holds one of the guild's configured admin roles."""
    if member.id == event.creator_id:
        return True
    if member.guild_permissions.manage_guild or member.guild_permissions.administrator:
        return True

    cfg = await get_or_create_guild_config(member.guild.id)
    admin_ids = set(cfg.admin_role_id_list())
    member_role_ids = {r.id for r in member.roles}
    return bool(admin_ids & member_role_ids)
