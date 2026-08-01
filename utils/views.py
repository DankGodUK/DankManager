import discord
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import Event, Signup, SignupStatus, BuildPreset


def build_signup_embed(event: Event, preset: BuildPreset | None, signups: list[Signup]) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {event.title}  (Event #{event.id})",
        description=f"**Type:** {event.content_type}\n**Starts:** <t:{int(event.start_time.timestamp())}:F>  (<t:{int(event.start_time.timestamp())}:R>)",
        color=discord.Color.orange(),
    )
    if preset:
        embed.add_field(name="Build", value=f"{preset.name} ({preset.size}-man)", inline=False)
        for slot in sorted(preset.slots, key=lambda s: s.order):
            accepted = [s for s in signups if s.status == SignupStatus.ACCEPTED and (s.assigned_role or s.requested_role) == slot.role_name]
            pending = [s for s in signups if s.status == SignupStatus.PENDING and s.requested_role == slot.role_name]
            value = f"{len(accepted)}/{slot.count} filled"
            if pending:
                value += f" ({len(pending)} pending)"
            embed.add_field(name=slot.role_name, value=value, inline=True)
    if event.voice_channel_id:
        embed.add_field(name="Voice Channel", value=f"<#{event.voice_channel_id}>", inline=False)
    embed.set_footer(text="Click Sign Up to pick a role. Officers: use /signup accept or /signup decline.")
    return embed


class RoleSelect(discord.ui.Select):
    def __init__(self, event_id: int, preset: BuildPreset):
        options = [
            discord.SelectOption(label=slot.role_name[:100], description=(slot.notes or "")[:100] or None)
            for slot in sorted(preset.slots, key=lambda s: s.order)
        ][:25]
        super().__init__(placeholder="Choose the role/build you want to sign up for...", options=options, custom_id=f"role_select:{event_id}")
        self.event_id = event_id

    async def callback(self, interaction: discord.Interaction):
        role_name = self.values[0]
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups)).where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
            if event is None:
                await interaction.response.send_message("❌ This event no longer exists.", ephemeral=True)
                return

            existing = next((s for s in event.signups if s.user_id == interaction.user.id), None)
            if existing:
                existing.requested_role = role_name
                existing.status = SignupStatus.PENDING
                existing.display_name = interaction.user.display_name
            else:
                session.add(Signup(
                    event_id=event.id,
                    user_id=interaction.user.id,
                    display_name=interaction.user.display_name,
                    requested_role=role_name,
                ))
            await session.commit()

        await interaction.response.send_message(
            f"✅ Signed up for **{role_name}** on **{self.view.event_title}**. Waiting for officer approval.",
            ephemeral=True,
        )
        await self.view.refresh_message(interaction)


class RoleSelectView(discord.ui.View):
    """Ephemeral view shown to a user picking their role."""
    def __init__(self, event_id: int, preset: BuildPreset, event_title: str, parent_message: discord.Message):
        super().__init__(timeout=120)
        self.event_title = event_title
        self.parent_message = parent_message
        self.add_item(RoleSelect(event_id, preset))

    async def refresh_message(self, interaction: discord.Interaction):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups), selectinload(Event.preset)).where(Event.id == int(self.children[0].custom_id.split(":")[1]))
            )
            event = result.scalar_one_or_none()
            if event is None:
                return
            preset = event.preset
        try:
            await self.parent_message.edit(embed=build_signup_embed(event, preset, event.signups))
        except discord.HTTPException:
            pass


class EventSignupView(discord.ui.View):
    """Persistent view attached to the main event post."""
    def __init__(self, event_id: int):
        super().__init__(timeout=None)
        self.event_id = event_id
        # custom_id embeds event_id so this works across restarts once re-added in on_ready
        self.sign_up.custom_id = f"event_signup:{event_id}"
        self.withdraw.custom_id = f"event_withdraw:{event_id}"

    @discord.ui.button(label="Sign Up", style=discord.ButtonStyle.success, emoji="⚔️")
    async def sign_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.preset).selectinload(BuildPreset.slots)).where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
        if event is None:
            await interaction.response.send_message("❌ This event no longer exists.", ephemeral=True)
            return
        if event.preset is None or not event.preset.slots:
            await interaction.response.send_message("❌ This event has no build preset roles configured.", ephemeral=True)
            return

        view = RoleSelectView(self.event_id, event.preset, event.title, interaction.message)
        await interaction.response.send_message("Pick your role:", view=view, ephemeral=True)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.danger, emoji="🚫")
    async def withdraw(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with async_session() as session:
            result = await session.execute(
                select(Event).options(selectinload(Event.signups), selectinload(Event.preset).selectinload(BuildPreset.slots))
                .where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()
            if event is None:
                await interaction.response.send_message("❌ This event no longer exists.", ephemeral=True)
                return
            signup = next((s for s in event.signups if s.user_id == interaction.user.id), None)
            if signup is None:
                await interaction.response.send_message("You aren't signed up for this event.", ephemeral=True)
                return
            await session.delete(signup)
            await session.commit()

            result = await session.execute(
                select(Event).options(selectinload(Event.signups), selectinload(Event.preset).selectinload(BuildPreset.slots))
                .where(Event.id == self.event_id)
            )
            event = result.scalar_one_or_none()

        await interaction.response.send_message("You've withdrawn from this event.", ephemeral=True)
        try:
            await interaction.message.edit(embed=build_signup_embed(event, event.preset, event.signups))
        except discord.HTTPException:
            pass
