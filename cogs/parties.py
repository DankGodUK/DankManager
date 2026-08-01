import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import Event, Signup, SignupStatus, PARTY_CAPACITY
from utils.permissions import is_event_manager


async def get_event(session, guild_id: int, event_id: int) -> Event | None:
    result = await session.execute(
        select(Event).options(selectinload(Event.signups)).where(Event.id == event_id, Event.guild_id == guild_id)
    )
    return result.scalar_one_or_none()


def party_headcount(event: Event, party_number: int) -> int:
    return sum(1 for s in event.signups if s.party_number == party_number and s.status == SignupStatus.ACCEPTED)


class PartyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    party_group = app_commands.Group(name="party", description="Manually manage party placement and roles for an event roster")

    @party_group.command(name="assign", description="Place an accepted signup into a specific party (max 20/party)")
    @app_commands.describe(event_id="Event ID", user="Player to assign", party="Party number (1, 2, 3...)")
    async def assign(self, interaction: discord.Interaction, event_id: int, user: discord.Member, party: app_commands.Range[int, 1, 10]):
        async with async_session() as session:
            event = await get_event(session, interaction.guild_id, event_id)
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if not await is_event_manager(interaction.user, event):
                await interaction.response.send_message("❌ You don't have permission to manage this event's roster.", ephemeral=True)
                return

            signup = next((s for s in event.signups if s.user_id == user.id), None)
            if signup is None or signup.status != SignupStatus.ACCEPTED:
                await interaction.response.send_message(f"❌ {user.mention} is not an accepted signup for this event.", ephemeral=True)
                return

            current_in_party = party_headcount(event, party)
            if current_in_party >= PARTY_CAPACITY and signup.party_number != party:
                await interaction.response.send_message(
                    f"❌ Party {party} is already full ({PARTY_CAPACITY}/{PARTY_CAPACITY}). Choose another party.",
                    ephemeral=True,
                )
                return

            signup.party_number = party
            if signup.assigned_role is None:
                signup.assigned_role = signup.requested_role
            await session.commit()

        await interaction.response.send_message(f"✅ {user.mention} assigned to **Party {party}**.", ephemeral=True)

    @party_group.command(name="move", description="Move an already-assigned player to a different party")
    async def move(self, interaction: discord.Interaction, event_id: int, user: discord.Member, party: app_commands.Range[int, 1, 10]):
        await self.assign.callback(self, interaction, event_id, user, party)

    @party_group.command(name="unassign", description="Remove a player from their current party (keeps them accepted)")
    async def unassign(self, interaction: discord.Interaction, event_id: int, user: discord.Member):
        async with async_session() as session:
            event = await get_event(session, interaction.guild_id, event_id)
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if not await is_event_manager(interaction.user, event):
                await interaction.response.send_message("❌ You don't have permission to manage this event's roster.", ephemeral=True)
                return
            signup = next((s for s in event.signups if s.user_id == user.id), None)
            if signup is None:
                await interaction.response.send_message("❌ No signup found for that user.", ephemeral=True)
                return
            signup.party_number = None
            await session.commit()
        await interaction.response.send_message(f"✅ {user.mention} unassigned from their party.", ephemeral=True)

    @party_group.command(name="setrole", description="Change the build/role a player is filling (e.g. swap them to Healer)")
    async def setrole(self, interaction: discord.Interaction, event_id: int, user: discord.Member, role: str):
        async with async_session() as session:
            event = await get_event(session, interaction.guild_id, event_id)
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return
            if not await is_event_manager(interaction.user, event):
                await interaction.response.send_message("❌ You don't have permission to manage this event's roster.", ephemeral=True)
                return
            signup = next((s for s in event.signups if s.user_id == user.id), None)
            if signup is None:
                await interaction.response.send_message("❌ No signup found for that user.", ephemeral=True)
                return
            signup.assigned_role = role
            await session.commit()
        await interaction.response.send_message(f"✅ {user.mention}'s role set to **{role}**.", ephemeral=True)

    @party_group.command(name="view", description="View the full party roster for an event")
    async def view(self, interaction: discord.Interaction, event_id: int):
        async with async_session() as session:
            # Need to selectinload preset and slots as well to get role_types
            from db.models import BuildPreset
            result = await session.execute(
                select(Event).options(selectinload(Event.signups), selectinload(Event.preset).selectinload(BuildPreset.slots)).where(Event.id == event_id, Event.guild_id == interaction.guild_id)
            )
            event = result.scalar_one_or_none()
            if event is None:
                await interaction.response.send_message("❌ Event not found.", ephemeral=True)
                return

            role_type_map = {}
            if event.preset:
                for slot in event.preset.slots:
                    role_type_map[slot.role_name] = getattr(slot, 'role_type', 'DPS') or 'DPS'

            accepted = [s for s in event.signups if s.status == SignupStatus.ACCEPTED]
            by_party = {}
            for s in accepted:
                by_party.setdefault(s.party_number, []).append(s)

            embed = discord.Embed(title=f"Roster — {event.title} (Event #{event.id})", color=discord.Color.blurple())
            
            role_order = {"Tank": 0, "Healer": 1, "Support": 2, "DPS": 3}
            from utils.views import ICONS
            
            for p_num in sorted([k for k in by_party.keys() if k is not None]):
                party_signups = by_party[p_num]
                by_type = {}
                for s in party_signups:
                    r_name = s.assigned_role or s.requested_role.split(",")[0]
                    r_type = role_type_map.get(r_name, "DPS")
                    by_type.setdefault(r_type, []).append((s, r_name))
                
                field_lines = []
                for r_type in sorted(by_type.keys(), key=lambda t: role_order.get(t, 99)):
                    icon = ICONS.get(r_type, "🔸")
                    players = []
                    for s, r_name in by_type[r_type]:
                        players.append(f"<@{s.user_id}> ({r_name})")
                    field_lines.append(f"{icon} **{r_type}**: {', '.join(players)}")
                    
                embed.add_field(
                    name=f"Party {p_num} ({len(party_signups)}/{PARTY_CAPACITY})",
                    value="\n".join(field_lines) or "Empty",
                    inline=False,
                )

            unassigned = by_party.get(None, [])
            if unassigned:
                by_type = {}
                for s in unassigned:
                    r_name = s.assigned_role or s.requested_role.split(",")[0]
                    r_type = role_type_map.get(r_name, "DPS")
                    by_type.setdefault(r_type, []).append((s, r_name))
                field_lines = []
                for r_type in sorted(by_type.keys(), key=lambda t: role_order.get(t, 99)):
                    icon = ICONS.get(r_type, "🔸")
                    players = []
                    for s, r_name in by_type[r_type]:
                        players.append(f"<@{s.user_id}> ({r_name})")
                    field_lines.append(f"{icon} **{r_type}**: {', '.join(players)}")
                    value="\n".join(field_lines) or "Empty",

            if not accepted:
                embed.description = "No accepted signups yet."

            await interaction.response.send_message(embed=embed)



async def setup(bot: commands.Bot):
    await bot.add_cog(PartyCog(bot))
