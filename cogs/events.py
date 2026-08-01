import datetime
from dateutil import parser as dateparser

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import Event, EventStatus, BuildPreset, Signup, SignupStatus
from utils.permissions import is_event_manager, get_or_create_guild_config
from utils.views import EventSignupView, build_signup_embed


class EventCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    event_group = app_commands.Group(name="event", description="Create and manage ZvZ / group content events")
    signup_group = app_commands.Group(name="signup", description="Review player signups")

    @event_group.command(name="create", description="Create a new event and post the signup embed")
    @app_commands.describe(
        title="Event title, e.g. 'Sunday ZvZ'",
        content_type="e.g. ZvZ, Small-scale, Hellgate, Avalon Roads",
        preset="Build preset to use for roles",
        start_time="Start time, UTC. Format: YYYY-MM-DD HH:MM (e.g. 2026-08-02 20:00)",
        voice_channel="Voice channel players should join",
        announce_channel="Where to post the signup embed (defaults to server config)",
    )
    async def create(
        self,
        interaction: discord.Interaction,
        title: str,
        content_type: str,
        preset: str,
        start_time: str,
        voice_channel: discord.VoiceChannel,
        announce_channel: discord.TextChannel = None,
    ):
        try:
            start_dt = dateparser.parse(start_time)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, OverflowError):
            await interaction.response.send_message("❌ Couldn't parse that start time. Try `YYYY-MM-DD HH:MM`.", ephemeral=True)
            return

        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset).options(selectinload(BuildPreset.slots))
                .where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            preset_obj = result.scalar_one_or_none()
            if preset_obj is None:
                await interaction.response.send_message(f"❌ No preset named **{preset}** found. Create one with `/preset create`.", ephemeral=True)
                return

            cfg = await get_or_create_guild_config(interaction.guild_id)
            target_channel = announce_channel or (interaction.guild.get_channel(cfg.announcement_channel_id) if cfg.announcement_channel_id else None) or interaction.channel

            event = Event(
                guild_id=interaction.guild_id,
                title=title,
                content_type=content_type,
                start_time=start_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None),
                creator_id=interaction.user.id,
                preset_id=preset_obj.id,
                voice_channel_id=voice_channel.id,
                announce_channel_id=target_channel.id,
            )
            session.add(event)
            await session.commit()  # flush populates event.id; a brand-new event has no signups yet, so we
                                     # pass that in directly below rather than touching event.signups/event.preset,
                                     # which would trigger an async-incompatible lazy load on this fresh object.

            embed = build_signup_embed(event, preset_obj, [])
            view = EventSignupView(event.id)
            msg = await target_channel.send(embed=embed, view=view)
            event.signup_message_id = msg.id
            session.add(event)
            await session.commit()

        await interaction.response.send_message(f"✅ Event **{title}** created (#{event.id}) and posted in {target_channel.mention}.", ephemeral=True)

    @event_group.command(name="list", description="List upcoming events")
    async def list_events(self, interaction: discord.Interaction):
        async with async_session() as session:
            result = await session.execute(
                select(Event).where(
                    Event.guild_id == interaction.guild_id,
                    Event.status.in_([EventStatus.OPEN, EventStatus.LOCKED]),
                ).order_by(Event.start_time)
            )
            events = result.scalars().all()
        if not events:
            await interaction.response.send_message("No upcoming events.", ephemeral=True)
            return
        embed = discord.Embed(title="Upcoming Events", color=discord.Color.blue())
        for e in events:
            ts = int(e.start_time.replace(tzinfo=datetime.timezone.utc).timestamp())
            embed.add_field(name=f"#{e.id} — {e.title}", value=f"{e.content_type} • <t:{ts}:F> • status: {e.status.value}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @event_group.command(name="lock", description="Close new signups for an event (existing ones can still be reviewed)")
    async def lock(self, interaction: discord.Interaction, event_id: int):
        await self._set_status(interaction, event_id, EventStatus.LOCKED)

    @event_group.command(name="cancel", description="Cancel an event")
    async def cancel(self, interaction: discord.Interaction, event_id: int):
        await self._set_status(interaction, event_id, EventStatus.CANCELLED)

    async def _set_status(self, interaction: discord.Interaction, event_id: int, status: EventStatus):
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.id == event_id, Event.guild_id == interaction.guild_id))
            event = result.scalar_one_or_none()
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if not await is_event_manager(interaction.user, event):
                await interaction.response.send_message("❌ You don't have permission to manage this event.", ephemeral=True)
                return
            event.status = status
            await session.commit()
        await interaction.response.send_message(f"✅ Event #{event_id} status set to **{status.value}**.", ephemeral=True)

    # ---------------- signup review ----------------

    @signup_group.command(name="pending", description="List pending signups for an event awaiting approval")
    async def pending(self, interaction: discord.Interaction, event_id: int):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups)).where(Event.id == event_id, Event.guild_id == interaction.guild_id)
            )
            event = result.scalar_one_or_none()
        if event is None:
            await interaction.response.send_message("❌ Event not found.", ephemeral=True)
            return
        pending_signups = [s for s in event.signups if s.status == SignupStatus.PENDING]
        if not pending_signups:
            await interaction.response.send_message("No pending signups.", ephemeral=True)
            return
        embed = discord.Embed(title=f"Pending Signups — {event.title}", color=discord.Color.yellow())
        for s in pending_signups:
            embed.add_field(name=s.display_name, value=f"<@{s.user_id}> — wants **{s.requested_role}**", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @signup_group.command(name="accept", description="Accept a player's signup")
    async def accept(self, interaction: discord.Interaction, event_id: int, user: discord.Member):
        await self._review(interaction, event_id, user, SignupStatus.ACCEPTED)

    @signup_group.command(name="decline", description="Decline a player's signup")
    async def decline(self, interaction: discord.Interaction, event_id: int, user: discord.Member, reason: str = None):
        await self._review(interaction, event_id, user, SignupStatus.DECLINED, reason)

    async def _review(self, interaction: discord.Interaction, event_id: int, user: discord.Member, new_status: SignupStatus, reason: str = None):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups), selectinload(Event.preset).selectinload(BuildPreset.slots))
                .where(Event.id == event_id, Event.guild_id == interaction.guild_id)
            )
            event = result.scalar_one_or_none()
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if not await is_event_manager(interaction.user, event):
                await interaction.response.send_message("❌ You don't have permission to review signups for this event.", ephemeral=True)
                return

            signup = next((s for s in event.signups if s.user_id == user.id), None)
            if signup is None:
                await interaction.response.send_message("❌ That user hasn't signed up for this event.", ephemeral=True)
                return

            signup.status = new_status
            if new_status == SignupStatus.ACCEPTED:
                signup.assigned_role = signup.assigned_role or signup.requested_role
            await session.commit()

            # refresh the posted embed
            if event.signup_message_id and event.announce_channel_id:
                channel = interaction.guild.get_channel(event.announce_channel_id)
                if channel:
                    try:
                        msg = await channel.fetch_message(event.signup_message_id)
                        await msg.edit(embed=build_signup_embed(event, event.preset, event.signups))
                    except discord.HTTPException:
                        pass

        verb = "accepted" if new_status == SignupStatus.ACCEPTED else "declined"
        await interaction.response.send_message(f"✅ {user.mention}'s signup {verb}.", ephemeral=True)

        try:
            if new_status == SignupStatus.ACCEPTED:
                await user.send(f"✅ You've been accepted for **{event.title}**! Ask an officer to confirm your party via `/party view`.")
            else:
                extra = f" Reason: {reason}" if reason else ""
                await user.send(f"Your signup for **{event.title}** was declined.{extra}")
        except discord.HTTPException:
            pass  # DMs closed, not critical

    @create.autocomplete("preset")
    async def preset_autocomplete(self, interaction: discord.Interaction, current: str):
        async with async_session() as session:
            result = await session.execute(select(BuildPreset.name).where(BuildPreset.guild_id == interaction.guild_id))
            names = [r[0] for r in result.all()]
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]

    async def cog_load(self):
        # Re-register persistent signup views for all open events so buttons keep working after a restart.
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.status == EventStatus.OPEN))
            events = result.scalars().all()
        for e in events:
            self.bot.add_view(EventSignupView(e.id))


async def setup(bot: commands.Bot):
    await bot.add_cog(EventCog(bot))
