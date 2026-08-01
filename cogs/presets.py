import re

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from db.engine import async_session
from db.models import BuildPreset, PresetSlot


def slots_total(preset: BuildPreset) -> int:
    return sum(s.count for s in preset.slots)


# Matches "Role Name x4", "Role Name x 4 | notes", "Role Name: 4", "Role Name:4 | notes"
_BULK_LINE_RE = re.compile(
    r"^\s*(?P<role>.+?)\s*(?:[xX×]\s*|:\s*)(?P<count>\d{1,2})\s*(?:\|\s*(?P<notes>.+))?\s*$"
)


def parse_bulk_roles(text: str) -> tuple[list[dict], list[str]]:
    """Parse multi-line/semicolon-separated 'Role x Count | notes' entries.
    Returns (parsed_entries, unparseable_raw_lines)."""
    raw_lines = [ln.strip() for chunk in text.split("\n") for ln in chunk.split(";")]
    raw_lines = [ln for ln in raw_lines if ln]

    parsed, errors = [], []
    for line in raw_lines:
        match = _BULK_LINE_RE.match(line)
        if not match:
            errors.append(line)
            continue
        role = match.group("role").strip()
        count = int(match.group("count"))
        notes = match.group("notes").strip() if match.group("notes") else None
        if not role or count < 1:
            errors.append(line)
            continue
        parsed.append({"role": role, "count": count, "notes": notes})
    return parsed, errors


async def apply_bulk_roles(guild_id: int, preset_name: str, roles_text: str) -> str:
    """Parses roles_text and writes slots to the named preset. Returns a result message."""
    parsed, errors = parse_bulk_roles(roles_text)

    if not parsed:
        return "❌ Couldn't parse any lines. Use one role per line like `Great Hammer Kite x4`."

    async with async_session() as session:
        result = await session.execute(
            select(BuildPreset)
            .options(selectinload(BuildPreset.slots))
            .where(BuildPreset.guild_id == guild_id, BuildPreset.name == preset_name)
        )
        p = result.scalar_one_or_none()
        if p is None:
            return f"❌ No preset named **{preset_name}** found."

        added, updated = [], []
        for entry in parsed:
            existing = next((s for s in p.slots if s.role_name.lower() == entry["role"].lower()), None)
            if existing:
                existing.count = entry["count"]
                if "type" in entry and entry["type"] != existing.role_type:
                    existing.role_type = entry["type"]
                if entry["notes"] is not None:
                    existing.notes = entry["notes"]
                updated.append(f"{existing.role_name} x{existing.count}")
            else:
                slot = PresetSlot(
                    preset_id=p.id,
                    role_name=entry["role"],
                    count=entry["count"],
                    role_type=entry.get("type", "DPS"),
                    order=len(p.slots) + len(added),
                    notes=entry["notes"],
                )
                session.add(slot)
                p.slots.append(slot)
                added.append(f"{entry['role']} x{entry['count']}")

        await session.commit()
        new_total = slots_total(p)
        preset_size = p.size

    lines = []
    if added:
        lines.append("✅ **Added:** " + ", ".join(added))
    if updated:
        lines.append("🔄 **Updated (already existed):** " + ", ".join(updated))
    if errors:
        lines.append("⚠️ **Couldn't parse:** " + " | ".join(errors))
    if new_total > preset_size:
        lines.append(f"⚠️ Slots now total **{new_total}**, which exceeds the preset size of **{preset_size}**.")
    return "\n".join(lines)


class BulkAddSlotModal(discord.ui.Modal, title="Bulk add roles"):
    def __init__(self, guild_id: int, preset_name: str):
        super().__init__()
        self.guild_id = guild_id
        self.preset_name = preset_name

    roles = discord.ui.TextInput(
        label="One role per line",
        style=discord.TextStyle.paragraph,
        placeholder="[Tank] Great Hammer Kite x4\n[Healer] Healer x8 | full dive spec\nLocksman x2",
        default="[Tank] Great Hammer Kite x4\n[Healer] Healer x8 | full dive spec\nLocksman x2",
        required=True,
        max_length=4000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        message = await apply_bulk_roles(self.guild_id, self.preset_name, self.roles.value)
        await interaction.response.send_message(message, ephemeral=True)



class PresetCreateSuccessView(discord.ui.View):
    def __init__(self, guild_id: int, preset_name: str):
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.preset_name = preset_name
        
    @discord.ui.button(label="Bulk Add Slots", style=discord.ButtonStyle.primary, emoji="📋")
    async def bulk_add(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Local import to avoid circular dependency issues if any, although BulkAddSlotModal is in the same file.
        await interaction.response.send_modal(BulkAddSlotModal(self.guild_id, self.preset_name))


class PresetCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    preset_group = app_commands.Group(name="preset", description="Manage reusable build presets for events")

    @preset_group.command(name="create", description="Create an empty build preset, e.g. 'ZVZ Core' (20-man)")
    @app_commands.describe(name="Name of the preset", size="Expected size of the group")
    async def create(self, interaction: discord.Interaction, name: str, size: app_commands.Range[int, 4, 40]):
        async with async_session() as session:
            existing = await session.execute(
                select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == name)
            )
            if existing.scalar_one_or_none():
                await interaction.response.send_message(f"❌ A preset named **{name}** already exists.", ephemeral=True)
                return

            preset = BuildPreset(guild_id=interaction.guild_id, name=name, size=size, created_by=interaction.user.id)
            session.add(preset)
            await session.commit()

        view = PresetCreateSuccessView(interaction.guild_id, name)
        await interaction.response.send_message(
            f"✅ Created preset **{name}** ({size}-man). Click below to bulk-add roles, or use `/preset addslot`.",
            view=view,
            ephemeral=True,
        )


    @preset_group.command(name="addslot", description="Add a single role line to a preset, e.g. 'Great Hammer Kite' x4")
    @app_commands.describe(preset="Preset name", role="Role/build name", role_type="Tank, DPS, Healer, Support", count="How many players in this role", notes="Optional gear/spec notes")
    @app_commands.choices(role_type=[
        app_commands.Choice(name="Tank", value="Tank"),
        app_commands.Choice(name="DPS", value="DPS"),
        app_commands.Choice(name="Healer", value="Healer"),
        app_commands.Choice(name="Support", value="Support"),
    ])
    async def addslot(self, interaction: discord.Interaction, preset: str, role: str, count: app_commands.Range[int, 1, 40], role_type: app_commands.Choice[str], notes: str = None):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset)
                .options(selectinload(BuildPreset.slots))
                .where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            p = result.scalar_one_or_none()
            if p is None:
                await interaction.response.send_message(f"❌ No preset named **{preset}** found.", ephemeral=True)
                return

            new_total = slots_total(p) + count
            slot = PresetSlot(preset_id=p.id, role_name=role, role_type=role_type.value, count=count, order=len(p.slots), notes=notes)
            session.add(slot)
            await session.commit()

        warn = ""
        if new_total > p.size:
            warn = f"\n⚠️ Slots now total **{new_total}**, which exceeds the preset size of **{p.size}**."
        await interaction.response.send_message(f"✅ Added **{role}** x{count} to **{preset}**.{warn}", ephemeral=True)

    @preset_group.command(name="bulkaddslot", description="Open a form to paste several role lines at once")
    @app_commands.describe(preset="Preset name")
    async def bulkaddslot(self, interaction: discord.Interaction, preset: str):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            if result.scalar_one_or_none() is None:
                await interaction.response.send_message(f"❌ No preset named **{preset}** found.", ephemeral=True)
                return

        await interaction.response.send_modal(BulkAddSlotModal(interaction.guild_id, preset))

    @preset_group.command(name="removeslot", description="Remove a role line from a preset by its role name")
    async def removeslot(self, interaction: discord.Interaction, preset: str, role: str):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset)
                .options(selectinload(BuildPreset.slots))
                .where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            p = result.scalar_one_or_none()
            if p is None:
                await interaction.response.send_message(f"❌ No preset named **{preset}** found.", ephemeral=True)
                return
            match = next((s for s in p.slots if s.role_name.lower() == role.lower()), None)
            if match is None:
                await interaction.response.send_message(f"❌ No role **{role}** on **{preset}**.", ephemeral=True)
                return
            await session.delete(match)
            await session.commit()
        await interaction.response.send_message(f"✅ Removed **{role}** from **{preset}**.", ephemeral=True)

    @preset_group.command(name="list", description="List all build presets on this server")
    async def list_presets(self, interaction: discord.Interaction):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id).order_by(BuildPreset.name)
            )
            presets = result.scalars().all()
        if not presets:
            await interaction.response.send_message("No presets yet. Create one with `/preset create`.", ephemeral=True)
            return
        embed = discord.Embed(title="Build Presets", color=discord.Color.green())
        for p in presets:
            embed.add_field(name=f"{p.name} ({p.size}-man)", value=f"Use `/preset view preset:{p.name}` for details", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @preset_group.command(name="view", description="View the full role breakdown of a preset")
    async def view(self, interaction: discord.Interaction, preset: str):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset)
                .options(selectinload(BuildPreset.slots))
                .where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            p = result.scalar_one_or_none()
        if p is None:
            await interaction.response.send_message(f"❌ No preset named **{preset}** found.", ephemeral=True)
            return

        total = slots_total(p)
        embed = discord.Embed(
            title=f"{p.name} ({p.size}-man)",
            description=f"Total slotted: {total}/{p.size}" + (" ⚠️ over target" if total > p.size else ""),
            color=discord.Color.gold(),
        )
        for s in sorted(p.slots, key=lambda x: x.order):
            value = f"x{s.count}"
            if s.notes:
                value += f" — {s.notes}"
            embed.add_field(name=s.role_name, value=value, inline=False)
        if not p.slots:
            embed.add_field(name="No roles added yet", value="Use `/preset addslot` or `/preset bulkaddslot`", inline=False)
        await interaction.response.send_message(embed=embed)

    @preset_group.command(name="delete", description="Delete a build preset")
    async def delete(self, interaction: discord.Interaction, preset: str):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset).where(BuildPreset.guild_id == interaction.guild_id, BuildPreset.name == preset)
            )
            p = result.scalar_one_or_none()
            if p is None:
                await interaction.response.send_message(f"❌ No preset named **{preset}** found.", ephemeral=True)
                return
            await session.delete(p)
            await session.commit()
        await interaction.response.send_message(f"🗑️ Deleted preset **{preset}**.", ephemeral=True)

    @addslot.autocomplete("preset")
    @bulkaddslot.autocomplete("preset")
    @removeslot.autocomplete("preset")
    @view.autocomplete("preset")
    @delete.autocomplete("preset")
    async def preset_name_autocomplete(self, interaction: discord.Interaction, current: str):
        async with async_session() as session:
            result = await session.execute(
                select(BuildPreset.name).where(BuildPreset.guild_id == interaction.guild_id)
            )
            names = [r[0] for r in result.all()]
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n.lower()][:25]


async def setup(bot: commands.Bot):
    await bot.add_cog(PresetCog(bot))
