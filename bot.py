import discord
import asyncio
import time
import random
import logging
import os
import re
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
MUDAE_BOT_ID = 432610292342587392  # Official Mudae bot ID

# ── Keep-alive web server ─────────────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    def log_message(self, *args):
        pass

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

        # Claim tracking
        self.can_claim = True
        self.claim_reset_at = None

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            logger.error(f'Channel {CHANNEL_ID} not found!')
            return
        logger.info(f'Connected to channel: #{self.channel.name}')
        self.is_running = True
        self.stop_flag = False
        asyncio.create_task(self.roll_loop())

    async def safe_send(self, msg):
        gap = time.time() - self.last_send_time
        if gap < 0.8:
            await asyncio.sleep(0.8 - gap)
        try:
            await self.channel.send(msg)
            self.last_send_time = time.time()
            return True
        except Exception as e:
            logger.error(f'Send failed: {e}')
            await asyncio.sleep(3)
            return False

    def is_unclaimed(self, embed):
        """Unclaimed = has a title, no 'belongs to' in footer."""
        if not embed or not embed.title:
            return False
        footer_text = embed.footer.text.lower() if embed.footer and embed.footer.text else ''
        if 'belongs to' in footer_text:
            return False
        # Must have description or fields to be a real character card
        if not embed.description and not embed.fields:
            return False
        return True

    def parse_claim_reset(self, content):
        match = re.search(r'(\d+)\s*min', content, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    async def claim_character(self, message):
        """
        Try to claim by:
        1. Clicking the 💞 component button if present (new Mudae style)
        2. Falling back to reacting with 💞 emoji (old Mudae style)
        """
        claimed = False

        # Method 1: Click the button component (Mudae's current style)
        if message.components:
            for row in message.components:
                for component in row.children:
                    # Look for the heart/claim button — usually emoji 💞 or label "Claim"
                    emoji = getattr(component, 'emoji', None)
                    label = getattr(component, 'label', '') or ''
                    emoji_name = str(emoji) if emoji else ''

                    if '💞' in emoji_name or 'claim' in label.lower():
                        try:
                            await component.click()
                            logger.info(f'✅ Claimed via button click!')
                            claimed = True
                        except Exception as e:
                            logger.warning(f'Button click failed: {e}')
                        break
                if claimed:
                    break

        # Method 2: React with 💞 (fallback for older Mudae embed style)
        if not claimed:
            try:
                await message.add_reaction('💞')
                logger.info(f'✅ Claimed via 💞 reaction!')
                claimed = True
            except Exception as e:
                logger.warning(f'Reaction failed: {e}')

        if not claimed:
            logger.warning('❌ Could not claim — neither button nor reaction worked.')

    async def roll_loop(self):
        logger.info('Roll loop started!')
        self.roll_count = 0

        while self.is_running and not self.stop_flag:
            try:
                # Check if claim cooldown expired
                if not self.can_claim and self.claim_reset_at:
                    if time.time() >= self.claim_reset_at:
                        self.can_claim = True
                        self.claim_reset_at = None
                        logger.info('✅ Claim cooldown expired — claiming re-enabled!')

                await self.safe_send('$wg')
                self.roll_count += 1
                self.total_rolls += 1

                if self.total_rolls % 50 == 0:
                    logger.info(f'Total rolls: {self.total_rolls} | Can claim: {self.can_claim}')

                if self.roll_count >= 20:
                    logger.info('20 rolls done — sending $us 20')
                    await asyncio.sleep(0.8)
                    await self.safe_send('$us 20')
                    self.roll_count = 0
                    await asyncio.sleep(3)

                await asyncio.sleep(random.uniform(0.9, 1.3))

            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        logger.info('Roll loop ended')
        self.is_running = False

    async def on_message(self, message):
        if message.channel.id != CHANNEL_ID:
            return

        # ── Mudae bot messages ────────────────────────────────────────────────
        if message.author.id == MUDAE_BOT_ID:

            # Check for unclaimed character embed
            if message.embeds and self.can_claim:
                for embed in message.embeds:
                    if self.is_unclaimed(embed):
                        char_name = embed.title or 'Unknown'
                        logger.info(f'🎯 Unclaimed: {char_name} — attempting claim...')
                        await asyncio.sleep(0.4)
                        await self.claim_character(message)
                        break

            # Check for "can't claim" text response
            content = message.content if message.content else ''
            content_lower = content.lower()
            if "can't claim" in content_lower or "cannot claim" in content_lower or "claim reset" in content_lower:
                minutes = self.parse_claim_reset(content)
                if minutes:
                    self.can_claim = False
                    self.claim_reset_at = time.time() + (minutes * 60)
                    logger.info(f'⏳ Claim locked for {minutes} min.')
                else:
                    self.can_claim = False
                    self.claim_reset_at = time.time() + 3600
                    logger.info('⏳ Claim locked — defaulting to 60 min cooldown.')
            return

        # ── Your commands ─────────────────────────────────────────────────────
        content = message.content.strip()
        if content.startswith('$'):
            return  # Ignore our own $wg/$us rolls

        cmd = content.lower()

        if cmd == '!stop':
            self.stop_flag = True
            self.is_running = False
            await message.channel.send(f'🛑 Stopped! Total rolls: {self.total_rolls}')

        elif cmd == '!start':
            if self.is_running:
                await message.channel.send('⚠️ Already running!')
                return
            self.stop_flag = False
            self.is_running = True
            await message.channel.send('▶️ Starting!')
            asyncio.create_task(self.roll_loop())

        elif cmd == '!status':
            if self.can_claim:
                claim_status = '✅ Ready'
            elif self.claim_reset_at:
                mins_left = int((self.claim_reset_at - time.time()) / 60)
                claim_status = f'⏳ Locked (~{mins_left} min left)'
            else:
                claim_status = '❌ Locked'
            status = '🟢 Running' if self.is_running else '🔴 Stopped'
            await message.channel.send(
                f'**Status:** {status}\n'
                f'**Total rolls:** {self.total_rolls}\n'
                f'**Batch:** {self.roll_count}/20\n'
                f'**Claiming:** {claim_status}'
            )

        elif cmd == '!reset':
            self.total_rolls = 0
            self.roll_count = 0
            await message.channel.send('🔄 Counters reset!')

        elif cmd == '!claimenable':
            self.can_claim = True
            self.claim_reset_at = None
            await message.channel.send('✅ Claiming re-enabled!')

        elif cmd == '!claimdisable':
            self.can_claim = False
            await message.channel.send('🚫 Claiming disabled!')


if __name__ == '__main__':
    t = threading.Thread(target=run_webserver, daemon=True)
    t.start()

    logger.info('Starting Mudae Roller Bot...')
    logger.info(f'Channel ID: {CHANNEL_ID}')

    bot = MudaeRoller()
    bot.run(TOKEN)
