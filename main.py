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
        async with aiosqlite.connect("blacklist.db") as db:
            await db.execute("CREATE TABLE IF NOT EXISTS blacklisted_users (user_id INTEGER PRIMARY KEY, nickname TEXT, roles TEXT, reason TEXT)")
            await db.execute("CREATE TABLE IF NOT EXISTS bot_permissions (user_id INTEGER PRIMARY KEY)")
            await db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            await db.commit()
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
        
        await interaction.response.defer()
        await run_blacklist_logic(self.channel, self.requester, self.target, self.reason, self.role_id)
        await interaction.edit_original_response(content="✅ Request Approved.", view=None)

    @discord.ui.button(label="Bail", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != OWNER_ID:
            return await interaction.response.send_message("Only owner can decline.", ephemeral=True)
        
        try:
            await self.requester.send(f"❌ **Request Declined:** Your request for {self.target.name} was rejected.")
        except: pass
        await interaction.response.edit_message(content="❌ Request Denied.", view=None)

# --- The Actual Styled Blacklist Action ---
async def run_blacklist_logic(channel, requester, target, reason, role_id):
    original_roles = ",".join([str(r.id) for r in target.roles if not r.is_default()])
    
    async with aiosqlite.connect("blacklist.db") as db:
        await db.execute("INSERT OR REPLACE INTO blacklisted_users VALUES (?, ?, ?, ?)", 
                         (target.id, target.display_name, original_roles, reason))
        await db.commit()
    
    bl_role = target.guild.get_role(int(role_id))
    try:
        # Edit the user
        await target.edit(roles=[bl_role], nick="Blacklisted")
        
        # --- STYLE MATCH: RED BLACKLIST EMBED ---
        embed = discord.Embed(title="🚫 Member Blacklisted (via Request)", color=0xed4245)
        embed.description = f"{target.mention} has been blacklisted"
        embed.add_field(name="User", value=f"{target.mention}\n({target.id})", inline=False)
        embed.add_field(name="Requested By", value=requester.mention, inline=True)
        embed.add_field(name="Accepted By", value=f"<@{OWNER_ID}>", inline=True)
        embed.add_field(name="Category", value="⚖️ Appealable", inline=False)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.add_field(name="Roles Stripped", value=f"{len(original_roles.split(',')) if original_roles else 0} roles stored", inline=False)
        
        embed.set_thumbnail(url=target.display_avatar.url)
        # Matches your timestamp format
        embed.set_footer(text=datetime.datetime.now().strftime('%m/%d/%Y %I:%M %p'))
        
        await channel.send(embed=embed)
    except Exception as e:
        await channel.send(f"❌ Failed to edit user: {e}")

# --- Commands ---

@bot.tree.command(name="setup", description="Owner Only: Set Blacklist Role ID")
async def setup(interaction: discord.Interaction, role_id: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    
    async with aiosqlite.connect("blacklist.db") as db:
        await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('blacklist_role', ?)", (role_id,))
        await db.commit()
    
    await interaction.response.send_message(f"✅ Blacklist Role set to `{role_id}`", ephemeral=True)

@bot.tree.command(name="permission", description="Owner Only: Authorize a user ID")
async def permission(interaction: discord.Interaction, user_id: str):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("❌ Owner only.", ephemeral=True)
    
    async with aiosqlite.connect("blacklist.db") as db:
        await db.execute("INSERT OR IGNORE INTO bot_permissions VALUES (?)", (int(user_id),))
        await db.commit()
    await interaction.response.send_message(f"✅ User `{user_id}` authorized.", ephemeral=True)

@bot.tree.command(name="blacklist", description="Blacklist a member")
async def blacklist(interaction: discord.Interaction, member: discord.Member, reason: str):
    if not await is_authorized(interaction.user.id):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    await interaction.response.defer(ephemeral=True)

    async with aiosqlite.connect("blacklist.db") as db:
        async with db.execute("SELECT value FROM settings WHERE key = 'blacklist_role'") as cursor:
            row = await cursor.fetchone()
            if not row: return await interaction.followup.send("❌ Run `/setup` first.")
            role_id = row[0]

    if interaction.user.id == OWNER_ID:
        await run_blacklist_logic(interaction.channel, interaction.user, member, reason, role_id)
        await interaction.followup.send("✅ Blacklist executed.", ephemeral=True)
    else:
        try:
            owner = await bot.fetch_user(OWNER_ID)
            view = BlacklistApproval(interaction.user, member, reason, interaction.channel, role_id)
            
            # --- REQUEST EMBED: CLEAN WHITE ---
            req_embed = discord.Embed(title="🛡️ New Blacklist Request", color=0xFFFFFF)
            req_embed.add_field(name="Target", value=member.mention, inline=True)
            req_embed.add_field(name="Requester", value=interaction.user.mention, inline=True)
            req_embed.add_field(name="Reason", value=reason, inline=False)
            
            await owner.send(embed=req_embed, view=view)
            await interaction.followup.send("📨 Request sent to owner.")
        except:
            await interaction.followup.send("❌ Failed to send request to owner.")

@bot.tree.command(name="unblacklist", description="Restore a member")
async def unblacklist(interaction: discord.Interaction, member: discord.Member):
    if not await is_authorized(interaction.user.id):
        return await interaction.response.send_message("❌ No permission.", ephemeral=True)

    await interaction.response.defer()

    async with aiosqlite.connect("blacklist.db") as db:
        async with db.execute("SELECT nickname, roles FROM blacklisted_users WHERE user_id = ?", (member.id,)) as cursor:
            row = await cursor.fetchone()
            if not row: return await interaction.followup.send("❌ Not blacklisted.")
            
            old_nick, roles_raw = row
            role_ids = [int(rid) for rid in roles_raw.split(",") if rid]
            restore = [interaction.guild.get_role(rid) for rid in role_ids if interaction.guild.get_role(rid)]

            try:
                await member.edit(roles=restore, nick=old_nick)
                await db.execute("DELETE FROM blacklisted_users WHERE user_id = ?", (member.id,))
                await db.commit()
                
                # --- STYLE MATCH: GREEN UNBLACKLIST EMBED ---
                embed = discord.Embed(title="✅ Member Unblacklisted", color=0x57f287)
                embed.description = f"{member.mention} has been unblacklisted and roles restored"
                embed.add_field(name="User", value=f"{member.mention} ({member.id})", inline=False)
                embed.add_field(name="Responsible Moderator", value=interaction.user.mention, inline=False)
                embed.add_field(name="Reason", value="Request approved.", inline=False)
                embed.add_field(name="Roles Restored", value=f"{len(restore)} roles", inline=False)
                embed.add_field(name="Nickname", value=f"✅ Restored to: {old_nick}", inline=False)
                
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=datetime.datetime.now().strftime('%m/%d/%Y %I:%M %p'))
                
                await interaction.followup.send(embed=embed)
            except Exception as e:
                await interaction.followup.send(f"❌ Error: {e}")

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')

bot.run(TOKEN)

