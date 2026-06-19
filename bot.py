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
MUDAE_BOT_ID = 432610292342587392

# ── Keep-alive ────────────────────────────────────────────────────────────────
class PingHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'Bot is running!')
    def log_message(self, *args):
        pass

def run_webserver():
    port = int(os.environ.get('PORT', 8080))
    HTTPServer(('0.0.0.0', port), PingHandler).serve_forever()

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

        # Claim state
        self.can_claim = True
        self.claim_reset_at = None        # epoch seconds when claim unlocks
        self.waiting_for_claim_reply = False  # True while we wait for Mudae's claim response

    async def on_ready(self):
        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            logger.error(f'Channel {CHANNEL_ID} not found!')
            return
        logger.info(f'Connected to #{self.channel.name}')
        self.is_running = True
        asyncio.create_task(self.roll_loop())

    async def safe_send(self, msg):
        gap = time.time() - self.last_send_time
        if gap < 0.8:
            await asyncio.sleep(0.8 - gap)
        try:
            await self.channel.send(msg)
            self.last_send_time = time.time()
        except Exception as e:
            logger.error(f'Send error: {e}')
            await asyncio.sleep(3)

    async def roll_loop(self):
        logger.info('Roll loop started!')
        self.roll_count = 0

        while self.is_running and not self.stop_flag:
            try:
                # Auto-unlock claim if timer expired
                if not self.can_claim and self.claim_reset_at:
                    if time.time() >= self.claim_reset_at:
                        self.can_claim = True
                        self.claim_reset_at = None
                        logger.info('✅ Claim unlocked!')

                await self.safe_send('$wg')
                self.roll_count += 1
                self.total_rolls += 1

                if self.total_rolls % 50 == 0:
                    logger.info(f'Rolls: {self.total_rolls} | Claim ready: {self.can_claim}')

                # Every 20 rolls reset uses
                if self.roll_count >= 20:
                    await asyncio.sleep(0.8)
                    await self.safe_send('$us 20')
                    self.roll_count = 0
                    logger.info('$us 20 sent')
                    await asyncio.sleep(3)

                await asyncio.sleep(random.uniform(0.9, 1.3))

            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        self.is_running = False
        logger.info('Roll loop stopped')

    async def on_message(self, message):
        if message.channel.id != CHANNEL_ID:
            return

        # ── Mudae responses ───────────────────────────────────────────────────
        if message.author.id == MUDAE_BOT_ID:

            # --- Claim result detection (runs before embed check) ---
            # If we just tried to claim, watch for Mudae's reply
            if self.waiting_for_claim_reply and message.content:
                content_lower = message.content.lower()

                if "can't claim" in content_lower or "cannot claim" in content_lower or "claim reset" in content_lower:
                    # Parse how many minutes until reset
                    match = re.search(r'(\d+)\s*min', message.content, re.IGNORECASE)
                    minutes = int(match.group(1)) if match else 60
                    self.can_claim = False
                    self.claim_reset_at = time.time() + (minutes * 60)
                    self.waiting_for_claim_reply = False
                    logger.info(f'⏳ Claim on cooldown for {minutes} min — rolling continues.')
                    return

                # Successful claim — Mudae edits the embed footer to "belongs to X"
                # We'll just clear the flag; the embed update handles the rest
                self.waiting_for_claim_reply = False

            # --- Character embed detection ---
            if message.embeds:
                embed = message.embeds[0]

                # Only act on embeds that look like character cards (have a title)
                if not embed.title:
                    return

                footer_text = (embed.footer.text or '').lower() if embed.footer else ''

                if 'belongs to' in footer_text:
                    # Already claimed — nothing to do
                    logger.debug(f'Claimed char: {embed.title} — skipping')
                    return

                # No "belongs to" in footer = unclaimed!
                if self.can_claim:
                    logger.info(f'🎯 Unclaimed: {embed.title} — clicking claim button...')
                    await asyncio.sleep(0.3)
                    claimed = await self.click_claim_button(message)
                    if claimed:
                        self.waiting_for_claim_reply = True
                else:
                    mins_left = max(0, int((self.claim_reset_at - time.time()) / 60)) if self.claim_reset_at else '?'
                    logger.info(f'Unclaimed char seen but claim locked (~{mins_left} min left) — skipping')

            return  # End of Mudae message handling

        # ── Your commands ─────────────────────────────────────────────────────
        raw = message.content.strip()
        if raw.startswith('$'):
            return  # Ignore our own roll commands

        cmd = raw.lower()

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
            asyncio.create_task(self.roll_loop())
            await message.channel.send('▶️ Started!')

        elif cmd == '!status':
            if self.can_claim:
                claim_str = '✅ Ready'
            elif self.claim_reset_at:
                mins_left = max(0, int((self.claim_reset_at - time.time()) / 60))
                claim_str = f'⏳ Locked (~{mins_left} min left)'
            else:
                claim_str = '❌ Locked'
            bot_status = '🟢 Running' if self.is_running else '🔴 Stopped'
            await message.channel.send(
                f'**Status:** {bot_status}\n'
                f'**Rolls:** {self.total_rolls} (batch {self.roll_count}/20)\n'
                f'**Claim:** {claim_str}'
            )

        elif cmd == '!claimenable':
            self.can_claim = True
            self.claim_reset_at = None
            await message.channel.send('✅ Claiming enabled!')

        elif cmd == '!claimdisable':
            self.can_claim = False
            await message.channel.send('🚫 Claiming disabled!')

        elif cmd == '!reset':
            self.total_rolls = 0
            self.roll_count = 0
            await message.channel.send('🔄 Counters reset!')

    async def click_claim_button(self, message):
        """
        Click the first button on a Mudae character embed.
        Mudae only puts one button on unclaimed chars — the claim button.
        It's a random Nitro emoji so we don't check what it looks like,
        we just click whatever button is there.
        Returns True if a button was found and clicked, False otherwise.
        """
        if message.components:
            for row in message.components:
                for component in row.children:
                    # Skip if it's not a clickable button
                    if not hasattr(component, 'click'):
                        continue
                    try:
                        await component.click()
                        emoji = getattr(component, 'emoji', None)
                        logger.info(f'✅ Clicked claim button! (emoji: {emoji})')
                        return True
                    except Exception as e:
                        logger.warning(f'Button click failed: {e}')
                        return False

        logger.warning('❌ No button found on this embed — Mudae may have changed format')
        return False


if __name__ == '__main__':
    threading.Thread(target=run_webserver, daemon=True).start()
    logger.info(f'Starting bot | Channel: {CHANNEL_ID}')
    MudaeRoller().run(TOKEN)
