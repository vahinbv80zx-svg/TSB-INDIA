import discord
from discord.ext import commands
import aiosqlite
import datetime
import os

# --- Configuration ---
TOKEN = os.getenv('DISCORD_TOKEN')
OWNER_ID = 1025704740828491806 

intents = discord.Intents.default()
intents.members = True 
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
DB_PATH = "blacklist.db"

# --- Persistent View for Approval Buttons ---
class BlacklistApproval(discord.ui.View):
    def __init__(self, requester, target, reason, original_channel, bl_role_id):
        super().__init__(timeout=None)
        self.requester = requester
        self.target = target
        self.reason = reason
        self.original_channel = original_channel
        self.bl_role_id = bl_role_id

    @discord.ui.button(label="Blacklist", style=discord.ButtonStyle.danger)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("You aren't the owner!", ephemeral=True)

        # 1. Save to DB
        original_roles = ",".join([str(r.id) for r in self.target.roles if not r.is_default()])
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR REPLACE INTO blacklisted_users VALUES (?, ?, ?, ?)", 
                             (self.target.id, self.target.display_name, original_roles, self.reason))
            await db.commit()

        # 2. Perform Blacklist
        bl_role = self.target.guild.get_role(int(self.bl_role_id))
        await self.target.edit(roles=[bl_role], nick="Blacklisted", reason=f"Approved by {interaction.user}")

        # 3. Send the Blacklist Embed to the original channel
        embed = discord.Embed(title="🚫 Member Blacklisted (via Request)", color=0xed4245)
        embed.add_field(name="User", value=f"{self.target.mention}\n({self.target.id})", inline=False)
        embed.add_field(name="Requested By", value=self.requester.mention, inline=True)
        embed.add_field(name="Accepted By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Category", value="⚖️ Appealable", inline=False)
        embed.add_field(name="Reason", value=self.reason, inline=False)
        embed.add_field(name="Roles Stripped", value=f"{len(original_roles.split(',')) if original_roles else 0} roles stored", inline=False)
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.set_footer(text=datetime.datetime.now().strftime('%m/%d/%Y %I:%M %p'))

        await self.original_channel.send(embed=embed)
        
        # Update the DM message
        await interaction.response.edit_message(content="✅ Request Approved.", view=None)

    @discord.ui.button(label="Bail", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("You aren't the owner!", ephemeral=True)

        # Notify the requester
        try:
            await self.requester.send(f"❌ **Request Declined:** Your request to blacklist {self.target.name} was rejected by the owner.")
        except:
            pass

        await interaction.response.edit_message(content="❌ Request Denied.", view=None)

# --- Bot Commands ---

@bot.command()
async def blacklist(ctx, member: discord.Member, *, reason="harassment"):
    if not await is_authorized(ctx):
        return await ctx.send("❌ No permission.")

    # Get Role ID from DB
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'blacklist_role'") as cursor:
            row = await cursor.fetchone()
            if not row:
                return await ctx.send("❌ Bot not setup. Owner must run a command to set the role.")
            role_id = row[0]

    # If the user IS the owner, bypass the request and do it immediately
    if ctx.author.id == OWNER_ID:
        # (Immediate logic here - keeping it simple for brevity, same as previous version)
        pass 

    # If the user is a Permissioned User, send request to Owner
    owner = await bot.fetch_user(OWNER_ID)
    
    # Clean White Embed for the Request
    request_embed = discord.Embed(title="🛡️ New Blacklist Request", color=0xFFFFFF)
    request_embed.add_field(name="Target User", value=f"{member.mention} ({member.id})", inline=False)
    request_embed.add_field(name="Requested By", value=f"{ctx.author.mention}", inline=True)
    request_embed.add_field(name="Reason", value=reason, inline=False)
    request_embed.set_thumbnail(url=member.display_avatar.url)
    request_embed.timestamp = datetime.datetime.now()

    view = BlacklistApproval(ctx.author, member, reason, ctx.channel, role_id)
    
    try:
        await owner.send(embed=request_embed, view=view)
        await ctx.send("📨 **Request Sent:** Your blacklist request has been sent to the owner for approval.")
    except:
        await ctx.send("❌ Error: I couldn't DM the owner to approve this.")

# --- Permissions Command (DM version) ---
@bot.command()
async def permission(ctx):
    if ctx.author.id != OWNER_ID:
        return await ctx.send("❌ Only Owner.")
    
    try:
        dm = await ctx.author.create_dm()
        await dm.send("🛡️ **Permission Setup:** Enter the User ID to authorize:")
        def check(m): return m.author == ctx.author and isinstance(m.channel, discord.DMChannel) and m.content.isdigit()
        msg = await bot.wait_for('message', check=check, timeout=60)
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO bot_permissions VALUES (?)", (int(msg.content),))
            await db.commit()
        await dm.send("✅ Authorized.")
    except:
        await ctx.send("❌ Check DMs.")

# (Unblacklist and helper functions remain the same as previous version)
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS blacklisted_users (user_id INTEGER PRIMARY KEY, nickname TEXT, roles TEXT, reason TEXT)")
        await db.execute("CREATE TABLE IF NOT EXISTS bot_permissions (user_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        await db.commit()

async def is_authorized(ctx):
    if ctx.author.id == OWNER_ID: return True
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bot_permissions WHERE user_id = ?", (ctx.author.id,)) as cursor:
            return await cursor.fetchone() is not None

@bot.event
async def on_ready():
    await init_db()
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)

