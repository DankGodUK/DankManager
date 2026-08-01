import datetime
import enum

from sqlalchemy import (
    BigInteger, String, Integer, ForeignKey, DateTime, Boolean, Enum, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

PARTY_CAPACITY = 20  # Albion Online hard cap per party


class Base(DeclarativeBase):
    pass


class GuildConfig(Base):
    """Per-guild settings: which roles count as 'admin' for accept/decline,
    and default channels."""
    __tablename__ = "guild_config"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    admin_role_ids: Mapped[str] = mapped_column(String, default="")  # comma-separated ids
    announcement_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    def admin_role_id_list(self) -> list[int]:
        return [int(x) for x in self.admin_role_ids.split(",") if x.strip()]


class BuildPreset(Base):
    """A reusable comp template, e.g. 'Clap Kite' (20-man) or 'Brawl' (40-man)."""
    __tablename__ = "build_preset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    name: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(Integer)  # total intended headcount, 4-40
    created_by: Mapped[int] = mapped_column(BigInteger)

    slots: Mapped[list["PresetSlot"]] = relationship(
        back_populates="preset", cascade="all, delete-orphan", order_by="PresetSlot.order", lazy="selectin"
    )

    __table_args__ = (UniqueConstraint("guild_id", "name", name="uq_preset_name_per_guild"),)


class PresetSlot(Base):
    """One role line within a build preset, e.g. 'Great Hammer Kite' x4."""
    __tablename__ = "preset_slot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    preset_id: Mapped[int] = mapped_column(ForeignKey("build_preset.id"))
    role_name: Mapped[str] = mapped_column(String)
    count: Mapped[int] = mapped_column(Integer)
    order: Mapped[int] = mapped_column(Integer, default=0)  # display / fill priority order
    notes: Mapped[str | None] = mapped_column(String, nullable=True)  # gear/spec notes

    preset: Mapped["BuildPreset"] = relationship(back_populates="slots")


class EventStatus(str, enum.Enum):
    OPEN = "open"
    LOCKED = "locked"          # signups closed, roster being finalized
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Event(Base):
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    title: Mapped[str] = mapped_column(String)
    content_type: Mapped[str] = mapped_column(String)  # e.g. "ZvZ", "Small-scale", "HG"
    start_time: Mapped[datetime.datetime] = mapped_column(DateTime)  # stored UTC
    creator_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), default=EventStatus.OPEN)

    preset_id: Mapped[int | None] = mapped_column(ForeignKey("build_preset.id"), nullable=True)
    voice_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    announce_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    signup_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    reminder_sent: Mapped[bool] = mapped_column(Boolean, default=False)

    preset: Mapped["BuildPreset | None"] = relationship(lazy="selectin")
    signups: Mapped[list["Signup"]] = relationship(back_populates="event", cascade="all, delete-orphan", lazy="selectin")


class SignupStatus(str, enum.Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    WAITLISTED = "waitlisted"


class Signup(Base):
    __tablename__ = "signup"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    display_name: Mapped[str] = mapped_column(String)  # cached at signup time for display

    requested_role: Mapped[str] = mapped_column(String)  # role name chosen from the preset
    status: Mapped[SignupStatus] = mapped_column(Enum(SignupStatus), default=SignupStatus.PENDING)

    # Party placement is 100% manual - set by creator/admin via /party assign
    party_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assigned_role: Mapped[str | None] = mapped_column(String, nullable=True)  # can differ from requested_role after a move

    reminded: Mapped[bool] = mapped_column(Boolean, default=False)

    event: Mapped["Event"] = relationship(back_populates="signups")

    __table_args__ = (UniqueConstraint("event_id", "user_id", name="uq_one_signup_per_user_per_event"),)
