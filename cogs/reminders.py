import datetime

import discord
from discord.ext import commands, tasks
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import Event, EventStatus, SignupStatus

REMINDER_WINDOW = datetime.timedelta(minutes=15)
CHECK_INTERVAL_SECONDS = 60


class ReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_reminders.start()

    def cog_unload(self):
        self.check_reminders.cancel()

    @tasks.loop(seconds=CHECK_INTERVAL_SECONDS)
    async def check_reminders(self):
        now = datetime.datetime.utcnow()
        window_end = now + REMINDER_WINDOW

        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups)).where(
                    Event.status.in_([EventStatus.OPEN, EventStatus.LOCKED]),
                    Event.reminder_sent == False,  # noqa: E712
                    Event.start_time <= window_end,
                    Event.start_time > now,
                )
            )
            due_events = result.scalars().all()

            for event in due_events:
                await self._send_reminder(event)
                event.reminder_sent = True
                session.add(event)
            if due_events:
                await session.commit()

    async def _send_reminder(self, event: Event):
        guild = self.bot.get_guild(event.guild_id)
        if guild is None:
            return

        voice_channel = guild.get_channel(event.voice_channel_id) if event.voice_channel_id else None
        accepted = [s for s in event.signups if s.status == SignupStatus.ACCEPTED]

        to_ping = []
        for signup in accepted:
            member = guild.get_member(signup.user_id)
            if member is None:
                continue
            # "Already in a voice channel" = skip. Change to `member.voice.channel != voice_channel`
            # if you want to require them specifically in the event's channel instead of any channel.
            if member.voice is None or member.voice.channel is None:
                to_ping.append(member)

        if not to_ping:
            return

        channel = guild.get_channel(event.announce_channel_id) if event.announce_channel_id else None
        if channel is None:
            return

        mentions = " ".join(m.mention for m in to_ping)
        vc_text = f" Join {voice_channel.mention}!" if voice_channel else ""
        await channel.send(
            f"⏰ **{event.title}** starts <t:{int(event.start_time.replace(tzinfo=datetime.timezone.utc).timestamp())}:R>!"
            f"{vc_text}\n{mentions}",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @check_reminders.before_loop
    async def before_check_reminders(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(ReminderCog(bot))
