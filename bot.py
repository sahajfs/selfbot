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
        self.rate_limited_until = 0      # epoch: pause rolling until this time

        # Claim state
        self.can_claim = True
        self.claim_reset_at = None
        self.waiting_for_claim_reply = False

        # Track recent Mudae roll message IDs we already tried to claim
        # so on_message_edit doesn't double-claim
        self.already_handled = set()

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
        """Send a message, respecting rate limit pauses."""
        # If we're rate limited, wait it out
        now = time.time()
        if now < self.rate_limited_until:
            wait = self.rate_limited_until - now
            logger.info(f'⏳ Rate limit active — waiting {wait:.1f}s before sending')
            await asyncio.sleep(wait)

        gap = time.time() - self.last_send_time
        if gap < 1.2:
            await asyncio.sleep(1.2 - gap)

        try:
            await self.channel.send(msg)
            self.last_send_time = time.time()
        except discord.errors.HTTPException as e:
            if e.status == 429:
                retry_after = float(getattr(e, 'retry_after', 5) or 5)
                logger.warning(f'🚫 Rate limited! Pausing {retry_after:.1f}s')
                self.rate_limited_until = time.time() + retry_after
                await asyncio.sleep(retry_after + 0.5)
            else:
                logger.error(f'HTTP error sending: {e}')
                await asyncio.sleep(3)
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

                # Every 20 rolls, reset uses
                if self.roll_count >= 20:
                    await asyncio.sleep(1.5)
                    await self.safe_send('$us 20')
                    self.roll_count = 0
                    logger.info('$us 20 sent — short pause')
                    await asyncio.sleep(4)
                    continue

                # Delay between rolls — slow enough to avoid 429s
                await asyncio.sleep(random.uniform(2.0, 3.0))

            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        self.is_running = False
        logger.info('Roll loop stopped')

    def _is_unclaimed_embed(self, embed):
        """True if embed looks like an unclaimed Mudae character."""
        if not embed or not embed.title:
            return False
        footer_text = (embed.footer.text or '').lower() if embed.footer else ''
        # Claimed = has "belongs to" in footer
        if 'belongs to' in footer_text:
            return False
        # Must have some content to be a real character card
        if not embed.description and not embed.fields and not embed.image:
            return False
        return True

    async def _try_claim(self, message):
        """Click the first available button on the embed (the claim button)."""
        if not message.components:
            logger.info('No components on message yet — will retry on edit')
            return False

        for row in message.components:
            for component in row.children:
                if not hasattr(component, 'click'):
                    continue
                try:
                    await component.click()
                    emoji = getattr(component, 'emoji', '?')
                    logger.info(f'✅ Clicked claim button! (emoji: {emoji})')
                    self.waiting_for_claim_reply = True
                    return True
                except discord.errors.HTTPException as e:
                    if e.status == 429:
                        retry_after = float(getattr(e, 'retry_after', 5) or 5)
                        logger.warning(f'🚫 Rate limited on button click — waiting {retry_after:.1f}s')
                        await asyncio.sleep(retry_after + 0.5)
                    logger.warning(f'Button click HTTP error: {e}')
                    return False
                except Exception as e:
                    logger.warning(f'Button click failed: {e}')
                    return False

        logger.warning('No clickable button found on embed')
        return False

    async def handle_mudae_embed(self, message):
        """Check embed footer and claim if unclaimed. Called on new message AND edits."""
        if not message.embeds:
            return

        embed = message.embeds[0]
        if not self._is_unclaimed_embed(embed):
            return

        # Don't double-claim the same message
        if message.id in self.already_handled:
            return

        if not self.can_claim:
            mins_left = max(0, int((self.claim_reset_at - time.time()) / 60)) if self.claim_reset_at else '?'
            logger.info(f'Unclaimed char "{embed.title}" — claim locked (~{mins_left} min left)')
            return

        # Mark as handled before clicking to prevent race condition
        self.already_handled.add(message.id)
        # Keep the set from growing forever
        if len(self.already_handled) > 200:
            self.already_handled = set(list(self.already_handled)[-100:])

        logger.info(f'🎯 Unclaimed: "{embed.title}" — attempting claim...')
        await asyncio.sleep(0.3)
        await self._try_claim(message)

    async def on_message(self, message):
        if message.channel.id != CHANNEL_ID:
            return

        # ── Mudae messages ────────────────────────────────────────────────────
        if message.author.id == MUDAE_BOT_ID:

            # Watch for claim result text
            if self.waiting_for_claim_reply and message.content:
                content_lower = message.content.lower()
                if "can't claim" in content_lower or "cannot claim" in content_lower or "claim reset" in content_lower:
                    match = re.search(r'(\d+)\s*min', message.content, re.IGNORECASE)
                    minutes = int(match.group(1)) if match else 60
                    self.can_claim = False
                    self.claim_reset_at = time.time() + (minutes * 60)
                    self.waiting_for_claim_reply = False
                    logger.info(f'⏳ Claim locked for {minutes} min — rolling continues')
                    return
                self.waiting_for_claim_reply = False

            await self.handle_mudae_embed(message)
            return

        # ── Your commands ─────────────────────────────────────────────────────
        raw = message.content.strip()
        if raw.startswith('$'):
            return

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

    async def on_message_edit(self, before, after):
        """
        Mudae adds the claim button by EDITING the message after sending.
        So we also check edited messages for unclaimed characters.
        """
        if after.channel.id != CHANNEL_ID:
            return
        if after.author.id != MUDAE_BOT_ID:
            return
        await self.handle_mudae_embed(after)


if __name__ == '__main__':
    threading.Thread(target=run_webserver, daemon=True).start()
    logger.info(f'Starting bot | Channel: {CHANNEL_ID}')
    MudaeRoller().run(TOKEN)
