import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload
import datetime
import dateparser

from db.engine import async_session
from db.models import Event, BuildPreset, EventStatus
from utils.permissions import is_event_manager, get_or_create_guild_config
from cogs.presets import BulkAddSlotModal, PresetCreateSuccessView

class CreatePresetModal(discord.ui.Modal, title="Create Preset"):
    preset_name = discord.ui.TextInput(
        label="Preset Name",
        placeholder="e.g. ZVZ Core",
        required=True,
        max_length=50
    )
    preset_size = discord.ui.TextInput(
        label="Preset Size (Players)",
        placeholder="e.g. 20",
        required=True,
        max_length=2
    )

    def __init__(self, guild_id: int):
        super().__init__()
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            size = int(self.preset_size.value)
            if size < 4 or size > 40:
                raise ValueError()
        except ValueError:
            await interaction.response.send_message("❌ Size must be a number between 4 and 40.", ephemeral=True)
            return

        async with async_session() as session:
            existing = await session.execute(
                select(BuildPreset).where(BuildPreset.guild_id == self.guild_id, BuildPreset.name == self.preset_name.value)
            )
            if existing.scalar_one_or_none():
                await interaction.response.send_message(f"❌ A preset named **{self.preset_name.value}** already exists.", ephemeral=True)
                return

            preset = BuildPreset(guild_id=self.guild_id, name=self.preset_name.value, size=size, created_by=interaction.user.id)
            session.add(preset)
            await session.commit()

        view = PresetCreateSuccessView(self.guild_id, self.preset_name.value)
        await interaction.response.send_message(
            f"✅ Created preset **{self.preset_name.value}** ({size}-man). Click below to bulk-add roles, or use `/preset addslot`.",
            view=view,
            ephemeral=True,
        )

class EventVoiceChannelSelectView(discord.ui.View):
    def __init__(self, title: str, content_type: str, preset_id: int, start_dt: datetime.datetime):
        super().__init__(timeout=300)
        self.event_title = title
        self.content_type = content_type
        self.preset_id = preset_id
        self.start_dt = start_dt

        self.channel_select = discord.ui.ChannelSelect(
            placeholder="Select a Voice Channel...",
            channel_types=[discord.ChannelType.voice],
            min_values=1, max_values=1
        )
        self.channel_select.callback = self.channel_select_callback
        self.add_item(self.channel_select)

    async def channel_select_callback(self, interaction: discord.Interaction):
        voice_channel = self.channel_select.values[0]
        
        async with async_session() as session:
            cfg = await get_or_create_guild_config(interaction.guild_id)
            target_channel = interaction.guild.get_channel(cfg.announcement_channel_id) if cfg.announcement_channel_id else interaction.channel
            
            result = await session.execute(
                select(BuildPreset).options(selectinload(BuildPreset.slots)).where(BuildPreset.id == self.preset_id)
            )
            preset_obj = result.scalar_one_or_none()

            event = Event(
                guild_id=interaction.guild_id,
                title=self.event_title,
                content_type=self.content_type,
                start_time=self.start_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None),
                creator_id=interaction.user.id,
                preset_id=self.preset_id,
                voice_channel_id=voice_channel.id,
                announce_channel_id=target_channel.id,
            )
            session.add(event)
            await session.commit()
            
            from utils.views import build_signup_embed
            from cogs.events import EventSignupView

            embed = build_signup_embed(event, preset_obj, [])
            view = EventSignupView(event.id)
            msg = await target_channel.send(embed=embed, view=view)
            event.signup_message_id = msg.id
            session.add(event)
            await session.commit()
            
        await interaction.response.edit_message(
            content=f"✅ Event **{self.event_title}** created successfully in <#{target_channel.id}>!",
            view=None
        )


class CreateEventPresetSelectView(discord.ui.View):
    def __init__(self, presets: list[BuildPreset]):
        super().__init__(timeout=120)
        options = [discord.SelectOption(label=p.name, description=f"{p.size}-man preset", value=str(p.id)) for p in presets[:25]]
        
        self.preset_select = discord.ui.Select(
            placeholder="Select a Preset for the Event...",
            options=options,
            min_values=1, max_values=1
        )
        self.preset_select.callback = self.preset_select_callback
        self.add_item(self.preset_select)

    async def preset_select_callback(self, interaction: discord.Interaction):
        preset_id = int(self.preset_select.values[0])
        await interaction.response.send_modal(CreateEventModal(interaction.guild_id, preset_id))

class CreateEventModal(discord.ui.Modal, title="Create Event (Step 1/2)"):
    event_title = discord.ui.TextInput(
        label="Event Title",
        placeholder="e.g. CTA 18:00 UTC",
        required=True,
        max_length=100
    )
    content_type = discord.ui.TextInput(
        label="Content Type",
        placeholder="e.g. ZvZ, Small-scale",
        required=True,
        max_length=50
    )
    start_time = discord.ui.TextInput(
        label="Start Time",
        placeholder="e.g. 2026-08-01 18:00 UTC",
        required=True,
        max_length=100
    )

    def __init__(self, guild_id: int, preset_id: int):
        super().__init__()
        self.guild_id = guild_id
        self.preset_id = preset_id

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_dt = dateparser.parse(self.start_time.value)
            if start_dt is None:
                raise ValueError("Could not parse")
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, OverflowError):
            await interaction.response.send_message("❌ Couldn't parse that start time. Try `YYYY-MM-DD HH:MM UTC`.", ephemeral=True)
            return

        view = EventVoiceChannelSelectView(
            title=self.event_title.value,
            content_type=self.content_type.value,
            preset_id=self.preset_id,
            start_dt=start_dt
        )
        await interaction.response.send_message(
            f"✅ Step 1 complete. Now, please select the **Voice Channel** for **{self.event_title.value}**.",
            view=view,
            ephemeral=True
        )

class ManageEventsSelect(discord.ui.Select):
    def __init__(self, events: list[Event]):
        options = [discord.SelectOption(label=f"#{e.id} {e.title}"[:100], description=f"{e.content_type} (Starts: {e.start_time.strftime('%Y-%m-%d %H:%M')})"[:100], value=str(e.id)) for e in events[:25]]
        super().__init__(placeholder="Select an event to manage...", options=options)

    async def callback(self, interaction: discord.Interaction):
        event_id = int(self.values[0])
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.id == event_id))
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
                
        view = EventManageActionView(event.id)
        await interaction.response.edit_message(content=f"Managing event: **{event.title}** (#{event.id})", view=view)


class EditEventModal(discord.ui.Modal, title="Edit Event"):
    def __init__(self, event: Event):
        super().__init__()
        self.event_id = event.id
        
        self.event_title = discord.ui.TextInput(
            label="Event Title",
            default=event.title,
            required=True,
            max_length=100
        )
        self.content_type = discord.ui.TextInput(
            label="Content Type",
            default=event.content_type,
            required=True,
            max_length=50
        )
        self.start_time = discord.ui.TextInput(
            label="Start Time (UTC)",
            default=event.start_time.strftime('%Y-%m-%d %H:%M'),
            required=True,
            max_length=100
        )
        self.add_item(self.event_title)
        self.add_item(self.content_type)
        self.add_item(self.start_time)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start_dt = dateparser.parse(self.start_time.value)
            if start_dt is None:
                raise ValueError("Could not parse")
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
        except (ValueError, OverflowError):
            await interaction.response.send_message("❌ Couldn't parse that start time. Try `YYYY-MM-DD HH:MM UTC`.", ephemeral=True)
            return

        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.preset).selectinload(BuildPreset.slots), selectinload(Event.signups))
                .where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
            
            event.title = self.event_title.value
            event.content_type = self.content_type.value
            event.start_time = start_dt.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            
            await session.commit()
            
            # Try to update the embed
            if event.announce_channel_id and event.signup_message_id:
                try:
                    channel = interaction.guild.get_channel(event.announce_channel_id)
                    if channel:
                        msg = await channel.fetch_message(event.signup_message_id)
                        from utils.views import build_signup_embed
                        embed = build_signup_embed(event, event.preset, event.signups)
                        await msg.edit(embed=embed)
                except Exception:
                    pass
            
        await interaction.response.send_message(f"✅ Event **{event.title}** has been updated.", ephemeral=True)

class EventManageActionView(discord.ui.View):
    def __init__(self, event_id: int):
        super().__init__(timeout=120)
        self.event_id = event_id

    @discord.ui.button(label="Edit Event", style=discord.ButtonStyle.secondary, emoji="✏️")
    async def edit_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.id == self.event_id))
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
            
            await interaction.response.send_modal(EditEventModal(event))

    @discord.ui.button(label="Close Event", style=discord.ButtonStyle.danger)
    async def close_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.id == self.event_id))
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
            event.status = EventStatus.CLOSED
            await session.commit()
            await interaction.response.edit_message(content=f"✅ Event **{event.title}** has been closed.", view=None)



    @discord.ui.button(label="Voice Check", style=discord.ButtonStyle.primary, emoji="🔊")
    async def voice_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups))
                .where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
                
            from db.models import SignupStatus
            accepted_signups = [s for s in event.signups if s.status == SignupStatus.ACCEPTED]
            if not accepted_signups:
                await interaction.response.send_message("No accepted signups yet.", ephemeral=True)
                return

            not_in_voice = []
            for signup in accepted_signups:
                # ignore fake test users
                if signup.user_id >= 1000000000:
                    continue
                member = interaction.guild.get_member(signup.user_id)
                if member:
                    if not member.voice or not member.voice.channel:
                        not_in_voice.append(member.mention)
                else:
                    not_in_voice.append(f"<@{signup.user_id}> (Not in server)")

            if not not_in_voice:
                await interaction.response.send_message("✅ Everyone is in a voice channel!", ephemeral=True)
            else:
                msg = f"**{len(not_in_voice)}** accepted users are not in a voice channel:\n" + ", ".join(not_in_voice)
                if len(msg) > 2000:
                    msg = msg[:1996] + "..."
                await interaction.response.send_message(msg, ephemeral=True)
                
    @discord.ui.button(label="Fill Test Signups", style=discord.ButtonStyle.secondary, emoji="🧪")
    async def fill_test_signups(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.preset).selectinload(BuildPreset.slots), selectinload(Event.signups))
                .where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
                
            if not event.preset:
                await interaction.response.send_message("Event has no preset.", ephemeral=True)
                return
                
            from db.models import Signup, SignupStatus
            
            # Start fake IDs from 1,000,000,000 to avoid real users
            fake_id_start = 1000000000
            fake_count = 0
            
            # Find how many signups we already have so we don't overlap fake IDs
            fake_id_start += len(event.signups)
            
            added_signups = 0
            for slot in event.preset.slots:
                # Count current signups for this role
                current_role_signups = [s for s in event.signups if s.requested_role == slot.role_name and s.status == SignupStatus.ACCEPTED]
                needed = slot.count - len(current_role_signups)
                
                for _ in range(needed):
                    signup = Signup(
                        event_id=event.id,
                        user_id=fake_id_start + fake_count,
                        display_name=f"TestUser_{fake_count}",
                        requested_role=slot.role_name,
                        status=SignupStatus.ACCEPTED
                    )
                    session.add(signup)
                    event.signups.append(signup)
                    fake_count += 1
                    added_signups += 1
                    
            if added_signups == 0:
                await interaction.response.send_message("Event is already full!", ephemeral=True)
                return
                
            await session.commit()
            
            # Update the embed
            if event.announce_channel_id and event.signup_message_id:
                try:
                    channel = interaction.guild.get_channel(event.announce_channel_id)
                    if channel:
                        msg = await channel.fetch_message(event.signup_message_id)
                        from utils.views import build_signup_embed
                        embed = build_signup_embed(event, event.preset, event.signups)
                        await msg.edit(embed=embed)
                except Exception:
                    pass
            
            await interaction.response.send_message(f"✅ Added {added_signups} fake signups to **{event.title}**.", ephemeral=True)

    async def cancel_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(select(Event).where(Event.id == self.event_id))
            event = result.scalar_one_or_none()
            if not event:
                await interaction.response.send_message("Event not found.", ephemeral=True)
                return
            event.status = EventStatus.CANCELLED
            await session.commit()
            await interaction.response.edit_message(content=f"✅ Event **{event.title}** has been cancelled.", view=None)

class ManagePresetsSelect(discord.ui.Select):

    def __init__(self, presets: list[BuildPreset]):
        options = [discord.SelectOption(label=p.name, description=f"{p.size}-man preset", value=str(p.id)) for p in presets[:25]]
        super().__init__(placeholder="Select a preset to edit...", options=options)

    async def callback(self, interaction: discord.Interaction):
        preset_id = int(self.values[0])
        async with async_session() as session:
            result = await session.execute(select(BuildPreset).where(BuildPreset.id == preset_id))
            preset = result.scalar_one_or_none()
            if not preset:
                await interaction.response.send_message("Preset not found.", ephemeral=True)
                return
                
        view = PresetManageActionView(interaction.guild_id, preset.name)
        await interaction.response.edit_message(content=f"Managing preset: **{preset.name}**", view=view)


class PresetManageActionView(discord.ui.View):
    def __init__(self, guild_id: int, preset_name: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.preset_name = preset_name


    @discord.ui.button(label="Bulk Add Slots", style=discord.ButtonStyle.primary, emoji="📋")
    async def bulk_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BulkAddSlotModal(self.guild_id, self.preset_name))

    @discord.ui.button(label="Delete Preset", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def delete_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(select(BuildPreset).where(BuildPreset.guild_id == self.guild_id, BuildPreset.name == self.preset_name))
            preset = result.scalar_one_or_none()
            if not preset:
                await interaction.response.send_message("Preset not found.", ephemeral=True)
                return
            await session.delete(preset)
            await session.commit()
            await interaction.response.edit_message(content=f"✅ Preset **{self.preset_name}** deleted.", view=None)



class ManagerPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="Create Event", style=discord.ButtonStyle.success, emoji="➕")
    async def create_event(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id))
            presets = result.scalars().all()
            
            if not presets:
                await interaction.response.send_message("❌ No presets found on this server. Please create a preset first.", ephemeral=True)
                return
                
            view = CreateEventPresetSelectView(presets)
            await interaction.response.send_message("Select a preset to use for this event:", view=view, ephemeral=True)

    @discord.ui.button(label="Manage Events", style=discord.ButtonStyle.primary, emoji="📅")
    async def manage_events(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            # We fetch events they have access to. 
            # We'll just fetch active ones for the guild, then filter in memory via permissions (or just let them see all, and check perms later, but checking in memory is fine).
            result = await session.execute(
                select(Event).where(Event.guild_id == interaction.guild_id, Event.status == EventStatus.OPEN).order_by(Event.start_time)
            )
            events = result.scalars().all()
            
            # Filter by manager perms
            manageable = []
            for e in events:
                if await is_event_manager(interaction.user, e):
                    manageable.append(e)
            

            if not manageable:
                await interaction.response.send_message("No active events you have permission to manage.", ephemeral=True)
                return
                
            view = discord.ui.View(timeout=120)
            view.add_item(ManageEventsSelect(manageable))
            await interaction.response.send_message("Select an event to manage:", view=view, ephemeral=True)


    @discord.ui.button(label="Create Preset", style=discord.ButtonStyle.success, emoji="➕")
    async def create_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreatePresetModal(interaction.guild_id))

    @discord.ui.button(label="Manage Presets", style=discord.ButtonStyle.secondary, emoji="📋")
    async def manage_presets(self, interaction: discord.Interaction, button: discord.ui.Button):
        # We need to check if they have admin roles
        # is_event_manager needs an Event, but they just want to manage presets. 
        # If they are admin, they can manage presets.
        # Let's just allow it for anyone with administrator or manage_guild for now, or check get_or_create_guild_config
        from utils.permissions import get_or_create_guild_config
        is_admin = interaction.user.guild_permissions.manage_guild or interaction.user.guild_permissions.administrator
        if not is_admin:
            cfg = await get_or_create_guild_config(interaction.guild.id)
            admin_ids = set(cfg.admin_role_id_list())
            member_role_ids = {r.id for r in interaction.user.roles}
            is_admin = bool(admin_ids & member_role_ids)
            
        if not is_admin:
            await interaction.response.send_message("❌ You don't have permission to manage presets.", ephemeral=True)
            return

        async with async_session() as session:
            result = await session.execute(select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id))
            presets = result.scalars().all()
            
            if not presets:
                await interaction.response.send_message("No presets found on this server.", ephemeral=True)
                return
                
            view = discord.ui.View(timeout=120)
            view.add_item(ManagePresetsSelect(presets))
            await interaction.response.send_message("Select a preset to manage:", view=view, ephemeral=True)


class ManagerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="manager", description="Open the Manager Panel to manage events and presets")
    async def manager(self, interaction: discord.Interaction):
        # Permissions will be checked per button press
        view = ManagerPanelView()
        await interaction.response.send_message("Welcome to the Manager Panel.", view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ManagerCog(bot))
