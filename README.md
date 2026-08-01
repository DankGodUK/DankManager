# Albion Online Event Bot

A Discord bot for running ZvZ / group-content sign-ups: build-sheet presets (4–40 man),
officer approval workflow, manual party placement (20-cap per party), and a 15-minute
voice-channel reminder ping.

## 1. Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: paste your bot token, optionally set DEV_GUILD_ID for instant command sync while testing
```

Create the bot at https://discord.com/developers/applications, enable these under
**Bot > Privileged Gateway Intents**:
- Server Members Intent (needed to look up accepted players)
- (Message Content is NOT required — everything is slash commands)

Invite it with the `bot` and `applications.commands` scopes and at minimum:
`Send Messages`, `Embed Links`, `Manage Messages`, `Read Message History`, `Connect` (to read voice states).

Run it:
```bash
python3 bot.py
```

## 2. Switching to Postgres later

Install `asyncpg` and change `DATABASE_URL` in `.env` to:
```
postgresql+asyncpg://user:password@host:5432/albion_bot
```
No code changes needed — SQLAlchemy's async engine handles both identically.

## 3. Typical setup flow

```
/config add_admin_role role:@Officer
/config set_announcement_channel channel:#events

/preset create name:"Clap Kite" size:20
/preset addslot preset:"Clap Kite" role:"Great Hammer Kite" count:4
/preset addslot preset:"Clap Kite" role:"Frost/Chainskite" count:6
/preset addslot preset:"Clap Kite" role:"Healer" count:4
/preset addslot preset:"Clap Kite" role:"Locksman" count:2
... etc up to 20

/preset create name:"Brawl" size:40
... add slots up to 40 (this build will span 2 parties)

/event create title:"Sunday ZvZ" content_type:"ZvZ" preset:"Clap Kite" start_time:"2026-08-03 20:00" voice_channel:#zvz-voice
```

This posts an embed with **Sign Up** / **Withdraw** buttons. Players click Sign Up,
pick their role from the dropdown, and land in `pending`.

## 4. Reviewing signups

```
/signup pending event_id:1
/signup accept event_id:1 user:@Player
/signup decline event_id:1 user:@Player reason:"Build's full"
```

Accepted players get DM'd and show up in the embed's per-role counts, but are **not**
auto-placed into a party — that's manual, on purpose (Party 2 overflow, comp balancing,
last-minute swaps, etc. are all judgment calls for the raid leader).

## 5. Party management (manual, 20-player cap enforced)

```
/party assign event_id:1 user:@Player party:1
/party assign event_id:1 user:@Player2 party:2        # e.g. once Party 1 hits 20
/party move   event_id:1 user:@Player party:2         # move between parties
/party setrole event_id:1 user:@Player role:"Healer"  # swap their assigned role
/party unassign event_id:1 user:@Player               # pull out of a party, stays accepted
/party view   event_id:1                               # full roster grouped by party
```

`/party assign` refuses if the target party already has 20 accepted players — you'll
get an error telling you to pick another party number. There's no cap on how many
party numbers you can use, so 40-man builds naturally become Party 1 + Party 2, and
you decide who goes where.

## 6. Reminders

A background task checks every minute for events starting in the next 15 minutes.
For each **accepted** player who isn't currently connected to *any* voice channel,
the bot posts a ping in the announcement channel. (Edit `cogs/reminders.py` if you'd
rather only skip players already in the event's *specific* voice channel — there's a
one-line comment marking exactly where to change that.)

## 7. Permissions model

A signup/party action is allowed if the user:
- created the event, OR
- has the `Manage Server` permission, OR
- holds a role added via `/config add_admin_role`

## Project layout

```
bot.py                 entry point
db/models.py            SQLAlchemy models (GuildConfig, BuildPreset, PresetSlot, Event, Signup)
db/engine.py             async engine/session, works with SQLite or Postgres via DATABASE_URL
cogs/config.py           admin roles & announcement channel
cogs/presets.py          build-sheet CRUD
cogs/events.py           event creation, signup review, posts the interactive embed
cogs/parties.py          manual party assign/move/setrole/view
cogs/reminders.py        15-minute voice-channel reminder loop
utils/permissions.py     shared "is this user allowed to manage this event" check
utils/views.py           Discord UI: sign-up button, role select dropdown, embed builder
```

## Notes / things you'll likely want to extend

- `start_time` is parsed as UTC if no timezone is given — consider adding a per-guild
  default timezone if your raid leaders aren't all in UTC.
- There's no automatic re-open of declined signups; a player can just click Sign Up
  again to resubmit.
- Waitlisting isn't wired up yet (the `SignupStatus.WAITLISTED` enum value exists but
  nothing sets it) — easy to add if you want a formal waitlist tier instead of just
  "accepted but unassigned."
