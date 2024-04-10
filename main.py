import discord
from discord.ext import commands, tasks
import json
import random
import string
from datetime import datetime, timedelta, timezone

# Load config from JSON
with open("config.json", "r") as config_file:
    config = json.load(config_file)

activity = discord.Activity(type=discord.ActivityType.streaming, name=config["status"], url="https://twitch.tv/ninjajodxd")
bot = commands.Bot(command_prefix=".", intents=discord.Intents.all(), help_command=None, activity=activity, guild_ids=[config["guilds"]])


# Background task to update slot expiration times
@tasks.loop(minutes=1)
async def update_slots():
    try:
        with open('occupied_slots.json', 'r') as f:
            data = json.load(f)

        now = datetime.now(timezone.utc)

        for channel_id, slot_data in list(data.items()):
            expiration_time = datetime.strptime(slot_data["expiration_date"], '%Y-%m-%d %H:%M:%S')
            if expiration_time <= now:
                channel = bot.get_channel(int(channel_id))
                await revoke_slot(channel, slot_data['key'])
                del data[channel_id]
        
        # Save updated data back to file
        with open('occupied_slots.json', 'w') as outfile:
            json.dump(data, outfile, indent=4)

    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"An error occurred while updating slots: {e}")

@update_slots.before_loop
async def before_update_slots():
    await bot.wait_until_ready()

async def revoke_slot(channel, key):
    if config.get("revoke_on_expire", True):
        try:
            await channel.set_permissions(
                channel.guild.default_role,
                read_messages=False
            )
            await channel.send(f"This Slot Is Revoked\nReason - Your Slot is Ended")
            
            with open('slot_keys.json', 'r') as f:
                keys_data = json.load(f)
            
            del keys_data[key]
            
            with open('slot_keys.json', 'w') as f:
                json.dump(keys_data, f, indent=4)
        
        except discord.Forbidden:
            await channel.send("I do not have permission to edit permissions in this channel.")
        
        except Exception as e:
            await channel.send(f"An error occurred: {e}")

@bot.event
async def on_ready():
    update_slots.start()
    print(f"{bot.user} is online!")

@bot.command()
async def gen(ctx, channel: discord.TextChannel, user: discord.Member, days: int):
    if ctx.author.id not in config["authorized_users"]:
        await ctx.send("Unauthorised")
        return

    try:
        with open('slots.json', 'r') as f:
            slots_data = json.load(f)
        
        channel_id = channel.id

        if not channel_id in [slot["channel_id"] for slot in slots_data["slot_channels"]]:
            await ctx.send("Specified channel is not a valid slot.")
            return

        key = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        if not key:
            await ctx.send("Failed to generate key. Please try again later.")
            return

        expiration_date = datetime.utcnow() + timedelta(days=days)

        try:
            await user.send(f"Key Gen\nKey - {key}\nSlot - {channel.mention}\nExpiration Date - {expiration_date.strftime('%Y-%m-%d %H:%M:%S')} UTC")
            await ctx.send(f"Sent Key To {user.mention} for {channel.mention}")

            # Generate a random unique ID for the slot
            slot_id = ''.join(random.choices(string.ascii_letters + string.digits, k=8))

            with open('slot_keys.json', 'r+') as f:
                keys_data = json.load(f)
                keys_data[key] = {
                    "channel_id": channel_id,
                    "expiration_date": expiration_date.strftime('%Y-%m-%d %H:%M:%S'),
                    "slot_id": slot_id  # Add slot ID here
                }
                f.seek(0)
                json.dump(keys_data, f, indent=4)

        except discord.HTTPException:
            await ctx.send("Failed to send key. Please check if the user has DMs enabled.")
        
        except Exception as e:
            await ctx.send(f"An error occurred: {e}")

    except FileNotFoundError:
        await ctx.send("No slots available to generate keys for.")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")


@bot.command()
async def slots(ctx):
    if ctx.author.id not in config["authorized_users"]:
        await ctx.send("Unauthorized")
        return
    try:
        with open('occupied_slots.json', 'r') as f:
            data = json.load(f)

        if not data:
            await ctx.send("No slots are currently active.")
            return

        embed = discord.Embed(title="Active Slots", color=discord.Color.green())

        for channel_id, slot_data in data.items():
            channel = bot.get_channel(int(channel_id))
            expiration_time = datetime.strptime(slot_data["expiration_date"], '%Y-%m-%d %H:%M:%S')
            remaining_time = expiration_time - datetime.utcnow()

            if remaining_time.total_seconds() <= 0:
                del data[channel_id]
                continue

            hours, remainder = divmod(remaining_time.seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            remaining_str = f"{hours}h {minutes}m {seconds}s"
            
            # Get the slot owner's ID from the slot data
            slot_owner_id = await get_slot_owner_id(slot_data["key"])
            
            embed.add_field(name=f"Slot {channel.mention}", value=f"Owner ID: {slot_owner_id}\nTime Left: {remaining_str}")

        await ctx.send(embed=embed)

        # Save updated data back to file (removing expired slots)
        with open('occupied_slots.json', 'w') as outfile:
            json.dump(data, outfile, indent=4)

    except FileNotFoundError:
        await ctx.send("No slots are currently active.")
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

# Function to get the slot owner's ID from the key
async def get_slot_owner_id(key):
    try:
        with open('slot_keys.json', 'r') as f:
            keys_data = json.load(f)
        
        if key in keys_data:
            return keys_data[key]["owner_id"]
    
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"An error occurred while getting slot owner ID: {e}")
    
    return "Unknown"

@bot.command()
async def usekey(ctx, key, new_slot_name):
    try:
        with open('slot_keys.json', 'r') as f:
            keys_data = json.load(f)
        
        if key in keys_data:
            channel_id = keys_data[key]["channel_id"]
            channel = ctx.guild.get_channel(channel_id)
            slotown_role_id = config.get('slot_owner_role')
            slotown_role = ctx.guild.get_role(slotown_role_id)
            
            if not slotown_role:
                return await ctx.send("Slot owner role is not properly configured. Please contact the server administrator.")

            if channel:
                if not ctx.guild.me.guild_permissions.manage_roles:
                    return await ctx.send("I don't have permission to manage roles.")

                await channel.set_permissions(
                    ctx.author,
                    read_messages=True,
                    send_messages=True,
                    mention_everyone=True
                )
                
                await channel.edit(name=new_slot_name)
                await ctx.send(f"Granted Slot To {ctx.author.mention}")
                
                # Add slot owner role to the user
                await ctx.author.add_roles(slotown_role)

                # Store slot data in occupied_slots.json
                with open('occupied_slots.json', 'r') as f:
                    slots_data = json.load(f)
                
                slots_data[str(channel_id)] = {
                    "key": key,
                    "expiration_date": keys_data[key]["expiration_date"],
                    "slot_id": keys_data[key]["slot_id"]  # Add slot ID here
                }

                with open('occupied_slots.json', 'w') as f:
                    json.dump(slots_data, f, indent=4)

                # Store the owner's ID in slot_keys.json
                keys_data[key]["owner_id"] = ctx.author.id
                with open('slot_keys.json', 'w') as outfile:
                    json.dump(keys_data, outfile, indent=4)

                # Send an embed message to the slot channel
                expiration_time = datetime.strptime(slots_data[str(channel_id)]["expiration_date"], '%Y-%m-%d %H:%M:%S')
                embed = discord.Embed(title="Premium Slot",
                                      description=f"Slot Owned By {ctx.author.mention}",
                                      color=discord.Color.green())
                embed.add_field(name="Owner ID", value=ctx.author.id, inline=False)
                embed.add_field(name="Owner Tag", value=ctx.author.mention, inline=False)
                embed.add_field(name="Slot ID", value=keys_data[key]["slot_id"], inline=False)
                embed.add_field(name="Slot Start Time", value=datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S %Z'), inline=False)
                embed.add_field(name="Slot End Time", value=expiration_time.strftime('%Y-%m-%d %H:%M:%S %Z'), inline=False)

                await channel.send(embed=embed)

            else:
                await ctx.send("Channel not found. Please contact the server administrator.")
        else:
            await ctx.send("Invalid key. Please check and try again.")
    
    except FileNotFoundError:
        await ctx.send("No keys found. Please generate keys first.")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")


@bot.command()
async def unhold(ctx, slot_id: str, member: discord.Member, *, reason):
    if ctx.author.id not in config["authorized_users"]:
        await ctx.send("Unauthorized")
        return
    
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.send("I don't have permission to manage roles.")

    try:
        with open('slots.json', 'r') as f:
            data = json.load(f)

        channel_id = next((slot["channel_id"] for slot in data["slot_channels"] if slot["slot_id"] == slot_id), None)
        if not channel_id:
            return await ctx.send("Slot not found.")

        slot_channel = ctx.guild.get_channel(channel_id)
        if not slot_channel:
            return await ctx.send("Slot channel not found.")

        await slot_channel.set_permissions(
            member,
            read_messages=True,
            send_messages=True,
            mention_everyone=True
        )
        await ctx.send(f"Slot Hold Is Removed\nReason - {reason}")
        await member.send(f"Slot Hold Is Removed\nReason - {reason}")

    except discord.Forbidden:
        await ctx.send("I do not have permission to edit permissions in this channel.")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def revoke(ctx, slot_id: str, member: discord.Member, *, reason):
    if ctx.author.id not in config["authorized_users"]:
        await ctx.send("Unauthorized")
        return
    
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.send("I don't have permission to manage roles.")

    try:
        with open('slots.json', 'r') as f:
            data = json.load(f)

        channel_id = next((slot["channel_id"] for slot in data["slot_channels"] if slot["slot_id"] == slot_id), None)
        if not channel_id:
            return await ctx.send("Slot not found.")

        slot_channel = ctx.guild.get_channel(channel_id)
        if not slot_channel:
            return await ctx.send("Slot channel not found.")

        await slot_channel.set_permissions(
            member,
            read_messages=True,
            send_messages=False,
            mention_everyone=False
        )
        await slot_channel.set_permissions(ctx.guild.default_role, read_messages=False)
        await ctx.send(f"This Slot Is Revoked\nReason - {reason}")
        await member.send(f"This Slot Is Revoked\nReason - {reason}")

    except discord.Forbidden:
        await ctx.send("I do not have permission to edit permissions in this channel.")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def hold(ctx, slot_id: str, member: discord.Member, *, reason):
    if ctx.author.id not in config["authorized_users"]:
        await ctx.send("Unauthorized")
        return
    
    if not ctx.guild.me.guild_permissions.manage_roles:
        return await ctx.send("I don't have permission to manage roles.")

    try:
        with open('slots.json', 'r') as f:
            data = json.load(f)

        channel_id = next((slot["channel_id"] for slot in data["slot_channels"] if slot["slot_id"] == slot_id), None)
        if not channel_id:
            return await ctx.send("Slot not found.")

        slot_channel = ctx.guild.get_channel(channel_id)
        if not slot_channel:
            return await ctx.send("Slot channel not found.")

        await slot_channel.set_permissions(
            member,
            read_messages=True,
            send_messages=False,
            mention_everyone=False
        )
        await ctx.send(f"Slot is On Hold\nReason - {reason}")
        await member.send(f"Slot is On Hold\nReason - {reason}")

    except discord.Forbidden:
        await ctx.send("I do not have permission to edit permissions in this channel.")
    
    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def createslot(ctx, slot_name: str):
    if ctx.author.id not in config["authorized_users"]:
        return await ctx.send("Unauthorized")

    guild = ctx.guild
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True)
    }

    try:
        channel = await guild.create_text_channel(name=slot_name, overwrites=overwrites)
        await ctx.send(f"Slot `{channel.name}` created successfully!")

        # Generate a random unique ID for the slot
        slot_id = ''.join(random.choices(string.ascii_letters + string.digits, k=2))

        with open('slots.json', 'r') as f:
            data = json.load(f)
        
        data["slot_channels"].append({
            "channel_id": channel.id,
            "channel_name": slot_name,
            "slot_id": slot_id
        })

        with open('slots.json', 'w') as f:
            json.dump(data, f, indent=4)

    except discord.Forbidden:
        await ctx.send("I do not have permission to create channels.")

    except Exception as e:
        await ctx.send(f"An error occurred: {e}")

@bot.command()
async def backupslots(ctx, category_id: int, position: int):
    if ctx.author.id not in config["authorized_users"]:
        return await ctx.send("Unauthorized")

    guild = ctx.guild
    slots_to_backup = []

    for channel in guild.channels:
        if isinstance(channel, discord.TextChannel) and channel.category_id == category_id and channel.position == position:
            slots_to_backup.append(channel)

    backup_data = []
    for slot in slots_to_backup:
        slot_data = {
            "name": slot.name,
            "permissions": {over[0].id: over[1].pair() for over in slot.overwrites}
        }
        backup_data.append(slot_data)
        await slot.delete()

    with open('slot_backup.json', 'w') as f:
        json.dump(backup_data, f, indent=4)

    await ctx.send("Slots backed up successfully!")

@bot.command()
async def restoreslots(ctx):
    if ctx.author.id not in config["authorized_users"]:
        return await ctx.send("Unauthorized")

    try:
        with open('slot_backup.json', 'r') as f:
            backup_data = json.load(f)

        for slot_data in backup_data:
            overwrites = {}
            for target_id, overwrite in slot_data["permissions"].items():
                target = ctx.guild.get_role(target_id) or ctx.guild.get_member(target_id)
                if target:
                    overwrites[target] = discord.PermissionOverwrite(**overwrite)

            category = ctx.guild.get_channel(config["slot_category"])
            if not category or not isinstance(category, discord.CategoryChannel):
                return await ctx.send("Slot category not found or invalid.")

            slot_channel = await category.create_text_channel(name=slot_data["name"], overwrites=overwrites)
        
        await ctx.send("Slots restored successfully!")

    except FileNotFoundError:
        await ctx.send("No slot backup found.")

    except Exception as e:
    
        await ctx.send(f"An error occurred: {e}")



@bot.command()
async def help(ctx):
    embed = discord.Embed(
        title="Bot Commands",
        description="Here are the available commands for this bot:",
        color=discord.Color.blue()
    )
    embed.add_field(
        name=".gen",
        value="Generate a key for a slot. Usage: `.gen <channel_mention> <user_mention> <days>`",
        inline=False
    )
    embed.add_field(
        name=".usekey",
        value="Use a generated key to access a slot. Usage: `.usekey <key> <new_slot_name>`",
        inline=False
    )
    embed.add_field(
        name=".hold",
        value="Put a slot on hold. Usage: `.hold <slot_id> <user_mention> <reason>`",
        inline=False
    )
    embed.add_field(
        name=".unhold",
        value="Remove hold from a slot. Usage: `.unhold <slot_id> <user_mention> <reason>`",
        inline=False
    )
    embed.add_field(
        name=".revoke",
        value="Revoke a slot. Usage: `.revoke <slot_id> <user_mention> <reason>`",
        inline=False
    )
    embed.add_field(
        name=".createslot",
        value="Create a new slot. Usage: `.createslot <slot_name>`",
        inline=False
    )
    embed.add_field(
        name=".backupslots",
        value="Backup slots with the same category and position. Usage: `.backupslots <category_id> <position>`",
        inline=False
    )
    embed.add_field(
        name=".restoreslots",
        value="Restore previously backed up slots. Usage: `.restoreslots`",
        inline=False
    )
    embed.add_field(
        name=".syncslot",
        value="Sync permissions for a slot. Usage: `.syncslot <slot_id>`",
        inline=False
    )
    embed.add_field(
        name=".slots",
        value="View active slots. Usage: `.slots`",
        inline=False
    )

    await ctx.send(embed=embed)


bot.run(config['bot_token'])
