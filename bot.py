import asyncio
import errno
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv
from mcrcon import MCRcon

load_dotenv()

TOKEN = os.getenv("TOKEN")
COMMAND_PREFIX = os.getenv("PREFIX", "!")
SERVER_IP = os.getenv("SERVER_IP", "")
SERVER_PORT = int(os.getenv("SERVER_PORT", 25565))
SERVER_SEED = os.getenv("SERVER_SEED", "")
RCON_HOST = os.getenv("RCON_HOST", "localhost")
RCON_PORT = int(os.getenv("RCON_PORT", 27757))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")
MAP_URL = "http://67.169.166.171:8100"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(
        command_prefix=COMMAND_PREFIX,
        intents=intents,
        help_command=None,
    )

    def member_role():
        return commands.has_role("member")

    def find_member_role(ctx: commands.Context) -> discord.Role | None:
        if ctx.guild is None:
            return None
        return discord.utils.get(ctx.guild.roles, name="member")

    def find_announcements_channel(ctx: commands.Context) -> discord.TextChannel | None:
        if ctx.guild is None:
            return None
        channel = discord.utils.get(ctx.guild.text_channels, name="announcements")
        if isinstance(channel, discord.TextChannel):
            return channel
        return None

    async def search_minecraft_wiki(term: str) -> dict | None:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": term,
            "srlimit": "1",
            "format": "json",
        }
        headers = {"User-Agent": "DiscordMinecraftServerBot/1.0"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://minecraft.wiki/api.php", params=params, timeout=10) as response:
                response.raise_for_status()
                data = await response.json()
        results = data.get("query", {}).get("search", [])
        if not results:
            return None
        return results[0]

    async def get_rcon_data() -> dict | None:
        """Connect via RCON and pull TPS, MSPT, player count, and mob count."""
        try:
            loop = asyncio.get_event_loop()

            def rcon_call():
                with MCRcon(RCON_HOST, RCON_PASSWORD, port=RCON_PORT) as mcr:
                    tps_raw = mcr.command("tps")
                    list_raw = mcr.command("list")
                    entity_raw = mcr.command("execute as @e[type=!player] run say x")
                return tps_raw, list_raw, entity_raw

            tps_raw, list_raw, entity_raw = await loop.run_in_executor(None, rcon_call)

            tps = "?"
            mspt = "?"
            for part in tps_raw.split(","):
                part = part.strip()
                if "TPS" in part.upper():
                    tps = part.split(":")[-1].strip().split()[0]
                if "MSPT" in part.upper() or "ms" in part:
                    mspt = part.split(":")[-1].strip().split()[0]

            player_count = 0
            for word in list_raw.split():
                if word.isdigit():
                    player_count = int(word)
                    break

            mob_count = len([line for line in entity_raw.strip().split("\n") if line.strip()])

            return {
                "tps": tps,
                "mspt": mspt,
                "player_count": player_count,
                "mob_count": mob_count,
            }
        except Exception as e:
            logging.warning("RCON failed: %s", e)
            return None

    @bot.event
    async def on_ready() -> None:
        logging.info("Logged in as %s", bot.user)
        activity = discord.Game(name=f"{COMMAND_PREFIX}help")
        await bot.change_presence(activity=activity)

    @bot.command(name="help")
    async def help_command(ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="Bot Commands",
            description="Here are the commands I can run right now.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=f"{COMMAND_PREFIX}ip", value="Show the server IP address. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}seed", value="Show the server seed. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}status", value="Check server status, TPS, MSPT, and player count. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}map", value="Get a link to the live BlueMap. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}memberadd @user", value="Give someone the member role. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}memberremove @user", value="Remove someone's member role. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}announce message", value="Post a clean announcement embed. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}wiki term", value="Show the top result from the vanilla Minecraft Wiki.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}coordinate x z location", value="Sends coords to pinned channel embed.", inline=False)
        await ctx.send(embed=embed)

    @bot.command(name="ip")
    @member_role()
    async def ip(ctx: commands.Context) -> None:
        if not SERVER_IP:
            await ctx.send("Server IP is not configured yet.")
            return
        await ctx.send(f"Server IP is {SERVER_IP}")

    @bot.command(name="map")
    @member_role()
    async def map_command(ctx: commands.Context) -> None:
        embed = discord.Embed(
            title="🗺️ Live Server Map",
            description=f"[Click here to open the map]({MAP_URL})",
            color=discord.Color.blue(),
        )
        embed.set_footer(text="Powered by BlueMap")
        await ctx.send(embed=embed)

    async def check_server(ip_address: str, port: int) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_address, port),
                timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    @bot.command(name="status")
    @member_role()
    async def status(ctx: commands.Context) -> None:
        await ctx.typing()

        online = await check_server(SERVER_IP, SERVER_PORT)

        if not online:
            embed = discord.Embed(
                title="🔴 Server Offline",
                description="The server is currently off.",
                color=discord.Color.red(),
            )
            await ctx.send(embed=embed)
            return

        # Server is up, try RCON for stats
        data = await get_rcon_data()

        embed = discord.Embed(
            title="🟢 Server Online",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )

        if data:
            player_count = data["player_count"]
            mob_count = data["mob_count"]
            mob_switch = "🟢 On" if mob_count > 300 and player_count < 5 else "🔴 Off"
            embed.add_field(name="TPS", value=data["tps"], inline=True)
            embed.add_field(name="MSPT", value=data["mspt"], inline=True)
            embed.add_field(name="Players", value=str(player_count), inline=True)
            embed.add_field(name="Mob Switch", value=mob_switch, inline=True)
        else:
            embed.add_field(name="Stats", value="Could not retrieve stats via RCON.", inline=False)

        await ctx.send(embed=embed)

    @bot.command(name="coordinate")
    @member_role()
    async def coordinate(ctx: commands.Context, x: int, z: int, *, location: str) -> None:
        channel = discord.utils.get(ctx.guild.text_channels, name="coordinates")
        if channel is None:
            await ctx.send("I could not find a channel named coordinates.")
            return

        pins = await channel.pins()
        existing = next((m for m in pins if m.author == bot.user), None)

        if existing is not None:
            old_description = existing.embeds[0].description if existing.embeds else ""
            new_line = f"\nX:{x} Z:{z} — {location}"
            new_description = old_description + new_line
            embed = discord.Embed(
                title="Server Coordinates",
                description=new_description,
                color=discord.Color.gold(),
            )
            await existing.edit(embed=embed)
        else:
            embed = discord.Embed(
                title="Server Coordinates",
                description=f"X:{x} Z:{z} — {location}",
                color=discord.Color.gold(),
            )
            msg = await channel.send(embed=embed)
            await msg.pin()

        await ctx.message.add_reaction("✅")

    @bot.command(name="seed")
    @member_role()
    async def seed(ctx: commands.Context) -> None:
        if not SERVER_SEED:
            await ctx.send("The server seed is not configured yet.")
            return
        await ctx.send(f"The server seed is: {SERVER_SEED}")

    @bot.command(name="memberadd")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def memberadd(ctx: commands.Context, target: discord.Member) -> None:
        role = find_member_role(ctx)
        if role is None:
            await ctx.send("I could not find a role named member.")
            return
        if role in target.roles:
            await ctx.send(f"{target.mention} already has the member role.")
            return
        try:
            await target.add_roles(role, reason=f"Added by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I do not have permission to give that role. Move my bot role above member in Server Settings.")
            return
        await ctx.send(f"Gave {target.mention} the member role.")

    @bot.command(name="memberremove")
    @commands.has_permissions(administrator=True)
    @commands.bot_has_permissions(manage_roles=True)
    async def memberremove(ctx: commands.Context, target: discord.Member) -> None:
        role = find_member_role(ctx)
        if role is None:
            await ctx.send("I could not find a role named member.")
            return
        if role not in target.roles:
            await ctx.send(f"{target.mention} does not have the member role.")
            return
        try:
            await target.remove_roles(role, reason=f"Removed by {ctx.author}")
        except discord.Forbidden:
            await ctx.send("I do not have permission to remove that role. Move my bot role above member in Server Settings.")
            return
        await ctx.send(f"Removed the member role from {target.mention}.")

    @bot.command(name="announce")
    @commands.has_permissions(administrator=True)
    async def announce(ctx: commands.Context, *, message: str) -> None:
        channel = find_announcements_channel(ctx)
        if channel is None:
            await ctx.send("I could not find a text channel named announcements.")
            return
        embed = discord.Embed(
            title="Announcement",
            description=message,
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Posted by {ctx.author.display_name}")
        await channel.send(embed=embed)
        await ctx.send(f"Announcement posted in {channel.mention}.")

    @bot.command(name="wiki")
    async def wiki(ctx: commands.Context, *, term: str) -> None:
        await ctx.typing()
        try:
            result = await search_minecraft_wiki(term)
        except aiohttp.ClientError:
            await ctx.send("I could not reach the Minecraft Wiki right now.")
            return
        if result is None:
            await ctx.send(f"No vanilla Minecraft Wiki result found for `{term}`.")
            return
        title = result["title"]
        page_url = f"https://minecraft.wiki/w/{quote(title.replace(' ', '_'))}"
        embed = discord.Embed(
            title=title,
            url=page_url,
            description="Top result from the vanilla Minecraft Wiki.",
            color=discord.Color.green(),
        )
        await ctx.send(embed=embed)

    @bot.event
    async def on_command_error(ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("That command is missing something. Try `!help`.")
            return
        if isinstance(error, commands.MissingRole):
            await ctx.send("You need the member role to use that command.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Only admins can use that command.")
            return
        if isinstance(error, commands.BotMissingPermissions):
            await ctx.send("I need the Manage Roles permission to do that.")
            return
        if isinstance(error, commands.MemberNotFound):
            await ctx.send("I could not find that member. Try mentioning them, like `!memberadd @username`.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("I could not understand that value. Try `!help`.")
            return
        if isinstance(error, commands.CommandNotFound):
            return
        logging.exception("Command failed", exc_info=error)
        await ctx.send("Something went wrong while running that command.")

    return bot


if __name__ == "__main__":
    bot = create_bot()
    bot.run(TOKEN)
