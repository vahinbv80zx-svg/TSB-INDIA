import discord
from discord.ext import commands
import aiosqlite
import datetime
import os

# --- Configuration ---
TOKEN = os.getenv('DISCORD_TOKEN')
OWNER_ID = 1025704740828491806  # Your ID set as Owner

intents = discord.Intents.default()
intents.members = True 
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Database Initialization ---
DB_PATH = "blacklist.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # User roles and data
        await db.execute('''CREATE TABLE IF NOT EXISTS blacklisted_users 
            (user_id INTEGER PRIMARY KEY, nickname TEXT, roles TEXT, reason TEXT)''')
        # Whitelisted users (Permissions)
        await db.execute('''CREATE TABLE IF NOT EXISTS bot_permissions (user_id INTEGER PRIMARY KEY)''')
        # Global settings (Blacklist Role ID)
        await db.execute('''CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)''')
        await db.commit()

@bot.event
async def on_ready():
    await init_db()
    print(f'Logged in as {bot.user} | Owner ID: {OWNER_ID}')

# --- Permission Logic ---
async def is_authorized(ctx):
    # Owner always has permission
    if ctx.author.id == OWNER_ID:
        return True
    # Check database for whitelisted users
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bot_permissions WHERE user_id = ?", (ctx.author.id,)) as cursor:
            return await cursor.fetchone() is not None

# --- Role Setup Logic ---
async def get_blacklist_role(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'blacklist_role'") as cursor:
            row = await cursor.fetchone()
            if row:
                return int(row[0])
            
            # If no ID exists, prompt for it
            await ctx.send("⚙️ **First-time Setup:** Please paste the **Role ID** for the Blacklist role.")
            
            def check(m):
                return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit()
            
            try:
                msg = await bot.wait_for('message', check=check, timeout=60.0)
                role_id = msg.content
                await db.execute("INSERT INTO settings (key, value) VALUES ('blacklist_role', ?)", (role_id,))
                await db.commit()
                await ctx.send(f"✅ Blacklist role saved (ID: {role_id}).")
                return int(role_id)
            except Exception:
                await ctx.send("❌ Setup timed out. Please try the command again.")
                return None

# --- Commands ---

@bot.command()
async def permission(ctx):
    """Owner only: Authorize a user ID to use the bot's mod commands."""
    if ctx.author.id != OWNER_ID:
        return await ctx.send("❌ Access Denied: Only the Bot Owner can grant permissions.")

    await ctx.send("Please enter the **User ID** of the person you want to authorize:")
    
    def check(m):
        return m.author == ctx.author and m.content.isdigit()
    
    try:
        msg = await bot.wait_for('message', check=check, timeout=30.0)
        u_id = int(msg.content)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO bot_permissions (user_id) VALUES (?)", (u_id,))
            await db.commit()
        await ctx.send(f"✅ User `{u_id}` is now authorized to use this bot anywhere.")
    except Exception:
        await ctx.send("❌ Request timed out.")

@bot.command()
async def blacklist(ctx, member: discord.Member, *, reason="harassment"):
    """Blacklists a member and strips their roles."""
    if not await is_authorized(ctx):
        return await ctx.send("❌ You don't have permission to use this command.")

    role_id = await get_blacklist_role(ctx)
    if not role_id: return
    
    bl_role = ctx.guild.get_role(role_id)
    if not bl_role:
        return await ctx.send("❌ Error: The saved Blacklist Role ID no longer exists in this server.")

    # Save original data
    original_roles = ",".join([str(r.id) for r in member.roles if not r.is_default()])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO blacklisted_users VALUES (?, ?, ?, ?)", 
                         (member.id, member.display_name, original_roles, reason))
        await db.commit()

    try:
        await member.edit(roles=[bl_role], reason=f"Blacklisted by {ctx.author}")
        
        embed = discord.Embed(title="🚫 Member Blacklisted", color=0xed4245)
        embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.set_footer(text=f"Roles stored: {len(original_roles.split(',')) if original_roles else 0}")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ Permission Error: My role must be higher than the user and the blacklist role.")

@bot.command()
async def unblacklist(ctx, member: discord.Member):
    """Restores original roles to a blacklisted member."""
    if not await is_authorized(ctx):
        return await ctx.send("❌ You don't have permission to use this command.")

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT nickname, roles FROM blacklisted_users WHERE user_id = ?", (member.id,)) as cursor:
            row = await cursor.fetchone()
            if not row:
                return await ctx.send("❌ This user is not in the blacklist records.")

            old_nick, roles_raw = row
            role_ids = [int(rid) for rid in roles_raw.split(",") if rid]
            
            # Map IDs to actual Role objects
            to_restore = [ctx.guild.get_role(rid) for rid in role_ids if ctx.guild.get_role(rid)]

            try:
                await member.edit(roles=to_restore, nick=old_nick)
                await db.execute("DELETE FROM blacklisted_users WHERE user_id = ?", (member.id,))
                await db.commit()

                embed = discord.Embed(title="✅ Member Unblacklisted", color=0x57f287)
                embed.description = f"Restored roles and nickname for {member.mention}."
                await ctx.send(embed=embed)
            except discord.Forbidden:
                await ctx.send("❌ Error: I cannot restore roles. Check my permissions.")

bot.run(TOKEN)

