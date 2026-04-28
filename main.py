import discord
from discord import app_commands
import aiosqlite
import datetime
import os

# --- Configuration ---
TOKEN = os.getenv('DISCORD_TOKEN')
OWNER_ID = 1025704740828491806 

class MyBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Initialize Database
        async with aiosqlite.connect("blacklist.db") as db:
            await db.execute("CREATE TABLE IF NOT EXISTS blacklisted_users (user_id INTEGER PRIMARY KEY, nickname TEXT, roles TEXT, reason TEXT)")
            await db.execute("CREATE TABLE IF NOT EXISTS bot_permissions (user_id INTEGER PRIMARY KEY)")
            await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            await db.commit()
        # Sync slash commands
        await self.tree.sync()

bot = MyBot()

# --- Permission Helper ---
async def is_authorized(user_id):
    if user_id == OWNER_ID: return True
    async with aiosqlite.connect("blacklist.db") as db:
        async with db.execute("SELECT 1 FROM bot_permissions WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchone() is not None

# --- UI Components ---
class BlacklistApproval(discord.ui.View):
    def __init__(self, requester, target, reason, channel, role_id):
        super().__init__(timeout=None)
        self.requester = requester
        self.target = target
        self.reason = reason
        self.channel = channel
        self.role_id = role_id

    @discord.ui.button(label="Blacklist", style=discord.ButtonStyle.danger)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("Only the owner can approve this.", ephemeral=True)
        
        # Logic to execute blacklist
        original_roles = ",".join([str(r.id) for r in self.target.roles if not r.is_default()])
        async with aiosqlite.connect("blacklist.db") as db:
            await db.execute("INSERT OR REPLACE INTO blacklisted_users VALUES (?, ?, ?, ?)", 
                             (self.target.id, self.target.display_name, original_roles, self.reason))
            await db.commit()

        bl_role = self.target.guild.get_role(int(self.role_id))
        await self.target.edit(roles=[bl_role], nick="Blacklisted")

        embed = discord.Embed(title="🚫 Member Blacklisted (via Request)", color=0xed4245)
        embed.add_field(name="User", value=f"{self.target.mention}\n({self.target.id})", inline=False)
        embed.add_field(name="Requested By", value=self.requester.mention, inline=True)
        embed.add_field(name="Accepted By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Category", value="⚖️ Appealable", inline=False)
        embed.add_field(name="Reason", value=self.reason, inline=False)
        embed.add_field(name="Roles Stripped", value=f"{len(original_roles.split(',')) if original_roles else 0} roles stored", inline=False)
        embed.set_thumbnail(url=self.target.display_avatar.url)
        embed.set_footer(text=datetime.datetime.now().strftime('%m/%d/%Y %I:%M %p'))

        await self.channel.send(embed=embed)
        await interaction.response.edit_message(content="✅ Request Approved.", view=None, embed=None)

    @discord.ui.button(label="Bail", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("Only the owner can decline this.", ephemeral=True)
        
        try:
            await self.requester.send(f"❌ **Request Declined:** Your request to blacklist {self.target.name} was rejected.")
        except: pass
        await interaction.response.edit_message(content="❌ Request Denied.", view=None, embed=None)

# --- Slash Commands ---

@bot.tree.command(name="permission", description="Authorize a user to use the bot (Owner Only)")
async def permission(interaction: discord.Interaction, user_id: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    
    async with aiosqlite.connect("blacklist.db") as db:
        await db.execute("INSERT OR IGNORE INTO bot_permissions VALUES (?)", (int(user_id),))
        await db.commit()
    
    await interaction.response.send_message(f"✅ User ID `{user_id}` authorized.", ephemeral=True)

@bot.tree.command(name="blacklist", description="Blacklist a member (Requires Approval)")
async def blacklist(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await is_authorized(interaction.user.id):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    async with aiosqlite.connect("blacklist.db") as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'blacklist_role'") as cursor:
            row = await cursor.fetchone()
            if not row:
                if interaction.user.id == OWNER_ID:
                    await interaction.response.send_message("⚙️ Check DMs to set the Blacklist Role ID.", ephemeral=True)
                    dm = await interaction.user.create_dm()
                    await dm.send("Please send the **Role ID** for blacklisting:")
                    def check(m): return m.author.id == OWNER_ID and isinstance(m.channel, discord.DMChannel) and m.content.isdigit()
                    msg = await bot.wait_for('message', check=check)
                    await db.execute("INSERT INTO settings VALUES ('blacklist_role', ?)", (msg.content,))
                    await db.commit()
                    return await dm.send("✅ Role saved. Run the command again.")
                return await interaction.response.send_message("❌ Bot not setup. Owner must run this once.", ephemeral=True)
            role_id = row[0]

    if interaction.user.id == OWNER_ID:
        # Immediate Blacklist for Owner
        original_roles = ",".join([str(r.id) for r in member.roles if not r.is_default()])
        async with aiosqlite.connect("blacklist.db") as db:
            await db.execute("INSERT OR REPLACE INTO blacklisted_users VALUES (?, ?, ?, ?)", 
                             (member.id, member.display_name, original_roles, reason))
            await db.commit()
        
        bl_role = member.guild.get_role(int(role_id))
        await member.edit(roles=[bl_role], nick="Blacklisted")
        
        embed = discord.Embed(title="🚫 Member Blacklisted", color=0xed4245)
        embed.add_field(name="User", value=f"{member.mention}\n({member.id})", inline=False)
        embed.add_field(name="Requested By", value=interaction.user.mention, inline=True)
        embed.add_field(name="Accepted By", value="Auto-Approved (Owner)", inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)
    else:
        # Request system for Authorized Users
        owner = await bot.fetch_user(OWNER_ID)
        req_embed = discord.Embed(title="🛡️ New Blacklist Request", color=0xFFFFFF)
        req_embed.add_field(name="Target", value=f"{member.mention}", inline=True)
        req_embed.add_field(name="Requester", value=f"{interaction.user.mention}", inline=True)
        req_embed.add_field(name="Reason", value=reason, inline=False)
        
        view = BlacklistApproval(interaction.user, member, reason, interaction.channel, role_id)
        await owner.send(embed=req_embed, view=view)
        await interaction.response.send_message("📨 Request sent to owner.", ephemeral=True)

@bot.tree.command(name="unblacklist", description="Restore a blacklisted member")
async def unblacklist(interaction: discord.Interaction, member: discord.Member):
    if not await is_authorized(interaction.user.id):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    async with aiosqlite.connect("blacklist.db") as db:
        async with db.execute("SELECT nickname, roles FROM blacklisted_users WHERE user_id = ?", (member.id,)) as cursor:
            row = await cursor.fetchone()
            if not row: return await interaction.response.send_message("❌ Not in records.", ephemeral=True)
            
            old_nick, roles_raw = row
            role_ids = [int(rid) for rid in roles_raw.split(",") if rid]
            restore = [interaction.guild.get_role(rid) for rid in role_ids if interaction.guild.get_role(rid)]

            await member.edit(roles=restore, nick=old_nick)
            await db.execute("DELETE FROM blacklisted_users WHERE user_id = ?", (member.id,))
            await db.commit()

            embed = discord.Embed(title="✅ Member Unblacklisted", color=0x57f287)
            embed.description = f"{member.mention} has been restored."
            embed.add_field(name="Restored Nickname", value=old_nick)
            embed.set_thumbnail(url=member.display_avatar.url)
            await interaction.response.send_message(embed=embed)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)
