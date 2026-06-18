import discord
from discord.ext import commands
import asyncio
import time
import random
import logging
import os
from dotenv import load_dotenv

# Load environment variables
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

# ===== LOAD CREDENTIALS =====
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
SERVER_ID = int(os.getenv('SERVER_ID', '0'))
# ===========================

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
        self.stop_flag = False
        self.pause_flag = False
        
    async def on_ready(self):
        """Called when bot is ready - DOES NOT AUTO-START"""
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            logger.error(f"Channel ID {CHANNEL_ID} not found!")
            return
            
        logger.info(f"Connected to channel: #{self.channel.name}")
        
        # ONLY SEND WELCOME MESSAGE - NO AUTO-START
        await self.channel.send("🤖 Bot is online! Type `!start` to begin rolling.")
        logger.info("Bot is ready! Waiting for !start command...")
    
    async def send_command(self, command):
        """Send a command with rate limiting"""
        try:
            # Check stop flag before sending
            if self.stop_flag:
                return False
            
            # Rate limiting
            current_time = time.time()
            if current_time - self.last_command_time < 1.0:
                wait_time = 1.0 - (current_time - self.last_command_time)
                for _ in range(int(wait_time * 10)):
                    if self.stop_flag:
                        return False
                    await asyncio.sleep(0.1)
            
            if self.stop_flag:
                return False
                
            await self.channel.send(command)
            self.last_command_time = time.time()
            logger.debug(f"Sent: {command}")
            return True
            
        except discord.errors.HTTPException as e:
            if e.status == 429:
                retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                logger.warning(f"Rate limited! Waiting {retry_after}s")
                for _ in range(int(retry_after * 2)):
                    if self.stop_flag:
                        return False
                    await asyncio.sleep(0.5)
                return await self.send_command(command)
            else:
                logger.error(f"HTTP error: {e}")
                return False
        except Exception as e:
            logger.error(f"Error sending command: {e}")
            return False
    
    async def roll_loop(self):
        """Main rolling loop"""
        logger.info("🔄 Roll loop started!")
        self.is_running = True
        self.roll_count = 0
        self.total_rolls = 0
        self.stop_flag = False
        
        await asyncio.sleep(2)
        
        while self.is_running and not self.stop_flag:
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
                        if self.stop_flag:
                            break
                            
                        logger.info(f"Used 20 rolls, executing $us 20")
                        await asyncio.sleep(0.5)
                        await self.send_command('$us 20')
                        self.roll_count = 0
                        logger.info(f"$us 20 executed. Total rolls: {self.total_rolls}")
                        
                        # 15 SECOND PAUSE
                        logger.info("⏳ 15 second pause - type !stop now!")
                        self.pause_flag = True
                        
                        for i in range(15):
                            if self.stop_flag:
                                logger.info("Stop detected during pause!")
                                self.pause_flag = False
                                break
                            await asyncio.sleep(1)
                            if (i + 1) % 5 == 0:
                                logger.info(f"⏳ {i+1}/15 seconds")
                        
                        self.pause_flag = False
                        if self.stop_flag:
                            break
                        logger.info("▶️ Resuming rolls...")
                
                # Wait between rolls - check stop during wait
                wait_time = 1.0 + random.uniform(-0.1, 0.1)
                for _ in range(int(wait_time * 10)):
                    if self.stop_flag:
                        break
                    await asyncio.sleep(0.1)
                
            except asyncio.CancelledError:
                logger.info("Roll loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in roll loop: {e}")
                await asyncio.sleep(2)
        
        # Clean up
        self.is_running = False
        self.roll_task = None
        logger.info(f"Roll loop ended. Total rolls: {self.total_rolls}")
    
    async def on_message(self, message):
        """Handle commands - THIS IS THE FIX"""
        # Ignore our own messages
        if message.author.id == self.user.id:
            return
            
        # Only process messages in designated channel
        if message.channel.id != CHANNEL_ID:
            return
            
        content = message.content.lower().strip()
        logger.info(f"📩 Received command: {content} from {message.author.name}")
        
        # ===== STOP COMMAND =====
        if content == '!stop':
            logger.info("🛑 !STOP COMMAND RECEIVED!")
            
            # Set stop flags immediately
            self.stop_flag = True
            self.is_running = False
            self.pause_flag = False
            
            # Cancel the task
            if self.roll_task:
                logger.info("Cancelling roll task...")
                self.roll_task.cancel()
                try:
                    await self.roll_task
                except asyncio.CancelledError:
                    pass
                self.roll_task = None
            
            # Send confirmation
            await message.channel.send(f"🛑 Stopped! Total rolls: {self.total_rolls}")
            logger.info(f"✅ Bot stopped. Total rolls: {self.total_rolls}")
            return
        
        # ===== START COMMAND =====
        if content == '!start':
            logger.info("▶️ !START COMMAND RECEIVED!")
            
            if self.is_running:
                await message.channel.send("Bot is already rolling!")
                return
            
            # Reset flags
            self.stop_flag = False
            self.is_running = True
            
            await message.channel.send("▶️ Starting rolling loop! Rolling $wg every second...")
            self.roll_task = asyncio.create_task(self.roll_loop())
            logger.info("Roll loop task created!")
            return
        
        # ===== STATUS COMMAND =====
        if content == '!status':
            status = "🟢 Running" if self.is_running else "🔴 Stopped"
            pause_status = "⏳ Paused" if self.pause_flag else "▶️ Active"
            await message.channel.send(
                f"**Bot Status:**\n"
                f"• Status: {status}\n"
                f"• Pause: {pause_status}\n"
                f"• Total Rolls: {self.total_rolls}\n"
                f"• Rolls since last $us: {self.roll_count}/20"
            )
            return
        
        # ===== HELP COMMAND =====
        if content == '!help':
            await message.channel.send(
                "**Commands:**\n"
                "• `!start` - Start rolling\n"
                "• `!stop` - Stop rolling\n"
                "• `!status` - Check status\n"
                "• `!help` - Show help\n\n"
                "**Features:**\n"
                "• Rolls $wg every second\n"
                "• $us 20 after every 20 rolls\n"
                "• 15 second pause after $us 20\n"
                "• Type !stop during pause to stop"
            )
            return

# Create bot instance
bot = MudaeRoller()

if __name__ == "__main__":
    try:
        if not TOKEN:
            logger.error("No Discord token found!")
            exit(1)
            
        if CHANNEL_ID == 0:
            logger.error("No channel ID found!")
            exit(1)
            
        logger.info("Starting Mudae Roller Bot...")
        bot.run(TOKEN)
        
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except discord.errors.LoginFailure:
        logger.error("Invalid token!")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        exit(1)
