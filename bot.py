import asyncio
import errno
import logging
from datetime import datetime, timezone
from urllib.parse import quote

import aiohttp
import discord
from discord.ext import commands

from discord_bot.settings import load_settings


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def create_bot() -> commands.Bot:
    settings = load_settings()

    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(
        command_prefix=settings.command_prefix,
        intents=intents,
        help_command=None,
    )

    def member_role() -> commands.has_role:
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

    async def check_server(ip_address: str, port: int) -> str:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(ip_address, port),
                timeout=3,
            )
            writer.close()
            await writer.wait_closed()
            return "active"
        except asyncio.TimeoutError:
            return "off"
        except OSError as error:
            if error.errno in {errno.ECONNREFUSED, errno.EHOSTUNREACH, errno.ENETUNREACH}:
                return "starting"
            return "off"

    @bot.event
    async def on_ready() -> None:
        logging.info("Logged in as %s", bot.user)
        activity = discord.Game(name=f"{settings.command_prefix}help")
        await bot.change_presence(activity=activity)

    @bot.command(name="help")
    async def help_command(ctx: commands.Context) -> None:
        prefix = settings.command_prefix
        embed = discord.Embed(
            title="Bot Commands",
            description="Here are the commands I can run right now.",
            color=discord.Color.blurple(),
        )
        embed.add_field(name=f"{prefix}ip", value="Show the server IP address. Requires the member role.", inline=False)
        embed.add_field(name=f"{prefix}seed", value="Show the server seed. Requires the member role.", inline=False)
        embed.add_field(name=f"{prefix}status", value="Check whether the server is active. Requires the member role.", inline=False)
        embed.add_field(name=f"{prefix}memberadd @user", value="Give someone the member role. Admins only.", inline=False)
        embed.add_field(name=f"{prefix}memberremove @user", value="Remove someone's member role. Admins only.", inline=False)
        embed.add_field(name=f"{prefix}announce message", value="Post a clean announcement embed. Admins only.", inline=False)
        embed.add_field(name=f"{prefix}wiki term", value="Show the top result from the vanilla Minecraft Wiki.", inline=False)
        embed.add_field(name=f"{prefix}coordinate", value="Sends coords to channel embed. Format as !coordiante x y location", inline=False)
        await ctx.send(embed=embed)

    @bot.command(name="ip")
    @member_role()
    async def ip(ctx: commands.Context) -> None:
        if not settings.server_ip:
            await ctx.send("server ip is not configured yet.")
            return
        await ctx.send(f"server ip is {settings.server_ip}")
    @bot.command(name="coordinate")
    @member_role()
    async def coordinate(ctx: commands.Context, x: int, z: int, *, location: str) -> None:
        channel = discord.utils.get(ctx.guild.text_channels, name="coordinates")
        if channel is None:
            await ctx.send("I could not find a channel named coordinates.")
            return

        # Find existing pinned coordinate embed from the bot
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
        if not settings.server_seed:
            await ctx.send("the server seed is not configured yet.")
            return
        await ctx.send(f"the server seed is: {settings.server_seed}")

    @bot.command(name="status")
    @member_role()
    async def status(ctx: commands.Context) -> None:
        if not settings.server_ip:
            await ctx.send("server ip is not configured yet.")
            return

        await ctx.typing()
        result = await check_server(settings.server_ip, settings.server_port)
        if result == "active":
            await ctx.send("🟢 server active")
        elif result == "starting":
            await ctx.send("🟡 server starting")
        else:
            await ctx.send("🔴 server off")

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

    bot._discord_token = settings.token
    return bot