import discord
from discord.ext import commands
import asyncio
import time
import random
import logging
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===== LOAD CREDENTIALS FROM .ENV =====
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
SERVER_ID = os.getenv('SERVER_ID')

# Convert to integers
if CHANNEL_ID:
    CHANNEL_ID = int(CHANNEL_ID)
if SERVER_ID:
    SERVER_ID = int(SERVER_ID)

# Debug: Check if variables loaded
logger.info(f"Token loaded: {'Yes' if TOKEN else 'No'}")
logger.info(f"Channel ID loaded: {CHANNEL_ID if CHANNEL_ID else 'No'}")
logger.info(f"Server ID loaded: {SERVER_ID if SERVER_ID else 'No'}")
# =====================================

class MudaeRoller(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix='!',
            self_bot=True,
            help_command=None
        )
        
        self.is_running = False
        self.roll_count = 0
        self.total_rolls = 0
        self.roll_task = None
        self.channel = None
        self.last_command_time = 0
        self.stop_requested = False
        
    async def on_ready(self):
        """Called when bot is ready - auto-starts rolling"""
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        
        # Get the channel
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            logger.error(f"Channel ID {CHANNEL_ID} not found!")
            logger.info("Available channels:")
            for guild in self.guilds:
                for channel in guild.channels:
                    logger.info(f"  #{channel.name} (ID: {channel.id})")
            return
            
        logger.info(f"Connected to channel: #{self.channel.name}")
        logger.info("Auto-starting roll loop...")
        
        # AUTO-START ROLLING
        self.roll_task = asyncio.create_task(self.roll_loop())
    
    async def send_command(self, command):
        """Send a command with rate limiting"""
        try:
            current_time = time.time()
            if current_time - self.last_command_time < 1.0:
                wait_time = 1.0 - (current_time - self.last_command_time)
                await asyncio.sleep(wait_time)
            
            await self.channel.send(command)
            self.last_command_time = time.time()
            logger.debug(f"Sent: {command}")
            return True
        except discord.errors.HTTPException as e:
            if e.status == 429:  # Rate limited
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                logger.warning(f"Rate limited! Waiting {retry_after}s")
                await asyncio.sleep(retry_after)
                return await self.send_command(command)
            else:
                logger.error(f"HTTP error: {e}")
                return False
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            return False
    
    async def roll_loop(self):
        """Main rolling loop - starts immediately"""
        logger.info("Roll loop started!")
        self.is_running = True
        self.roll_count = 0
        self.total_rolls = 0
        self.stop_requested = False
        
        await asyncio.sleep(2)  # Initial delay before starting
        
        while self.is_running and not self.stop_requested:
            try:
                # Send roll command
                success = await self.send_command('$wg')
                
                if success:
                    self.roll_count += 1
                    self.total_rolls += 1
                    
                    if self.total_rolls % 100 == 0:
                        logger.info(f"Total rolls: {self.total_rolls}")
                    
                    # Check if we need to use $us
                    if self.roll_count >= 20:
                        logger.info(f"Used 20 rolls, executing $us 20")
                        await asyncio.sleep(random.uniform(0.5, 1.0))
                        await self.send_command('$us 20')
                        self.roll_count = 0
                        logger.info(f"$us 20 executed. Total rolls: {self.total_rolls}")
                
                # Wait exactly 1 second between rolls
                wait_time = 1.0 + random.uniform(-0.1, 0.1)
                await asyncio.sleep(max(0.8, wait_time))
                
            except asyncio.CancelledError:
                logger.info("Roll loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in roll loop: {e}")
                await asyncio.sleep(2)
        
        logger.info("Roll loop ended")
        self.is_running = False
    
    async def on_message(self, message):
        """Handle commands from user"""
        # Ignore our own messages
        if message.author.id == self.user.id:
            return
            
        # Only process messages in designated channel
        if message.channel.id != CHANNEL_ID:
            return
            
        content = message.content.lower().strip()
        
        if content == '!stop':
            if not self.is_running:
                await message.channel.send("Bot is already stopped!")
                return
            
            logger.info("Stop command received - stopping roll loop...")
            self.stop_requested = True
            self.is_running = False
            
            if self.roll_task:
                self.roll_task.cancel()
                try:
                    await self.roll_task
                except asyncio.CancelledError:
                    pass
                self.roll_task = None
            
            await message.channel.send(f"Stopped rolling. Total rolls performed: {self.total_rolls}")
            logger.info(f"Bot stopped. Total rolls: {self.total_rolls}")
            
        elif content == '!start':
            if self.is_running:
                await message.channel.send("Bot is already rolling!")
                return
            
            logger.info("Start command received - starting roll loop...")
            self.stop_requested = False
            await message.channel.send("Starting rolling loop!")
            self.roll_task = asyncio.create_task(self.roll_loop())
            
        elif content == '!status':
            status = "Running" if self.is_running else "Stopped"
            await message.channel.send(
                f"Bot Status:\n"
                f"Status: {status}\n"
                f"Total Rolls: {self.total_rolls}\n"
                f"Rolls since last $us: {self.roll_count}/20"
            )
            
        elif content == '!help':
            await message.channel.send(
                "Commands:\n"
                "!start - Start rolling\n"
                "!stop - Stop rolling\n"
                "!status - Check status\n"
                "!help - Show help"
            )

# Create bot instance
bot = MudaeRoller()

if __name__ == "__main__":
    try:
        # Check if variables are loaded
        if not TOKEN:
            logger.error("❌ No Discord token found! Check .env file")
            logger.info("Make sure .env file exists with: DISCORD_TOKEN=your_token_here")
            exit(1)
            
        if not CHANNEL_ID:
            logger.error("❌ No channel ID found! Check .env file")
            logger.info("Make sure .env file exists with: CHANNEL_ID=your_channel_id_here")
            exit(1)
            
        logger.info("🚀 Starting Mudae Roller Bot...")
        logger.info(f"📌 Channel ID: {CHANNEL_ID}")
        bot.run(TOKEN)
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user")
    except discord.errors.LoginFailure:
        logger.error("❌ Invalid token! Please check your token.")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        exit(1)