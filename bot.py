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
from rcon.source import Client as RconClient

load_dotenv()

TOKEN = os.getenv("TOKEN")
COMMAND_PREFIX = os.getenv("PREFIX", "!")
SERVER_IP = os.getenv("SERVER_IP", "")
SERVER_PORT = int(os.getenv("SERVER_PORT", 25565))
SERVER_SEED = os.getenv("SERVER_SEED", "")
RCON_HOST = os.getenv("RCON_HOST", "localhost")
RCON_PORT = int(os.getenv("RCON_PORT", 27757))
RCON_PASSWORD = os.getenv("RCON_PASSWORD", "")
MAP_URL = "https://map.stoypass.xyz/"

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
                with RconClient(RCON_HOST, port=RCON_PORT, passwd=RCON_PASSWORD) as client:
                    tps_raw = client.run("tps")
                    list_raw = client.run("list")
                    entity_raw = client.run("execute as @e[type=!player] run say x")
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
        embed.add_field(name=f"{COMMAND_PREFIX}rlip", value="Show the rlcraft server IP. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}status", value="Check server status, TPS, MSPT, and player count. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}map", value="Get a link to the live BlueMap. Requires the member role.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}memberadd @user", value="Give someone the member role. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}memberremove @user", value="Remove someone's member role. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}announce message", value="Post a clean announcement embed. Admins only.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}wiki term", value="Show the top result from the vanilla Minecraft Wiki.", inline=False)
        embed.add_field(name=f"{COMMAND_PREFIX}rlwiki term", value="Show the top