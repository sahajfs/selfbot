import discord
import asyncio
import time
import random
import logging
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))

# ── Keep-alive web server (fixes Render 30-min shutdown) ──────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')

    def log_message(self, *args):
        pass  # Silence HTTP logs

def run_webserver():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), PingHandler)
    logger.info(f'Keep-alive server on port {port}')
    server.serve_forever()

# ── Bot ───────────────────────────────────────────────────────────────────────
class MudaeRoller(discord.Client):
    def __init__(self):
        super().__init__()
        self.is_running = False
        self.roll_count = 0
        self.total_rolls = 0
        self.channel = None
        self.last_send_time = 0
        self.stop_flag = False

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            logger.error(f'Channel {CHANNEL_ID} not found!')
            return
        logger.info(f'Connected to channel: #{self.channel.name}')
        logger.info('Auto-starting roll loop...')
        self.is_running = True
        self.stop_flag = False
        asyncio.create_task(self.roll_loop())

    async def safe_send(self, msg):
        """Send with rate-limit spacing."""
        gap = time.time() - self.last_send_time
        if gap < 1.5:
            await asyncio.sleep(1.5 - gap)
        try:
            await self.channel.send(msg)
            self.last_send_time = time.time()
            return True
        except Exception as e:
            logger.error(f'Send failed: {e}')
            await asyncio.sleep(3)
            return False

    async def roll_loop(self):
        logger.info('Roll loop started!')
        self.roll_count = 0

        while self.is_running and not self.stop_flag:
            try:
                await self.safe_send('$wg')
                self.roll_count += 1
                self.total_rolls += 1

                if self.total_rolls % 100 == 0:
                    logger.info(f'Total rolls: {self.total_rolls}')

                if self.roll_count >= 20:
                    logger.info('Used 20 rolls, executing $us 20')
                    await asyncio.sleep(1.0)
                    await self.safe_send('$us 20')
                    self.roll_count = 0
                    logger.info(f'$us 20 executed. Total rolls: {self.total_rolls}')

                    # Pause between batches — helps avoid 429s
                    pause = random.uniform(12, 18)
                    logger.info(f'Pausing {pause:.1f}s between batches...')
                    for _ in range(int(pause)):
                        if self.stop_flag:
                            break
                        await asyncio.sleep(1)

                # Random delay between rolls (reduces rate limiting)
                await asyncio.sleep(random.uniform(1.5, 2.5))

            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        logger.info('Roll loop ended')
        self.is_running = False

    async def on_message(self, message):
        # Only listen in the target channel
        if message.channel.id != CHANNEL_ID:
            return

        # FIX: selfbot means YOU are the bot — so we must NOT skip your messages.
        # Instead, only skip messages that look like bot commands we just sent
        # ($wg, $us) to avoid self-triggering on our own roll commands.
        content = message.content.strip()
        if content.startswith('$'):
            return  # Ignore our own roll commands

        cmd = content.lower()

        if cmd == '!stop':
            self.stop_flag = True
            self.is_running = False
            await message.channel.send(f'🛑 Stopped! Total rolls: {self.total_rolls}')
            logger.info('Stopped by !stop command')

        elif cmd == '!start':
            if self.is_running:
                await message.channel.send('⚠️ Already running!')
                return
            self.stop_flag = False
            self.is_running = True
            await message.channel.send('▶️ Starting rolls!')
            asyncio.create_task(self.roll_loop())
            logger.info('Started by !start command')

        elif cmd == '!status':
            status = '🟢 Running' if self.is_running else '🔴 Stopped'
            await message.channel.send(
                f'Status: {status}\n'
                f'Total rolls: {self.total_rolls}\n'
                f'Current batch: {self.roll_count}/20'
            )

        elif cmd == '!reset':
            self.total_rolls = 0
            self.roll_count = 0
            await message.channel.send('🔄 Counters reset!')


if __name__ == '__main__':
    # Start keep-alive server in background thread
    t = threading.Thread(target=run_webserver, daemon=True)
    t.start()

    logger.info('Starting Mudae Roller Bot...')
    logger.info(f'Channel ID: {CHANNEL_ID}')

    bot = MudaeRoller()
    bot.run(TOKEN)
