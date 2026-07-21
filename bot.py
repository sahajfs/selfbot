import discord
import asyncio
import time
import random
import logging
import re
import threading
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── LOAD FROM .ENV ──────────────────────────────────────────────────────
TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))
SERVER_ID = os.getenv('SERVER_ID')
MUDAE_BOT_ID = 432610292342587392

# Roll pacing — tune these two if you still see rate limits or laggy commands.
# Higher = safer for typing commands, lower = faster rolling.
ROLL_DELAY_MIN = float(os.getenv('ROLL_DELAY_MIN', '1.1'))
ROLL_DELAY_MAX = float(os.getenv('ROLL_DELAY_MAX', '1.6'))

# ALL rolling types
CLAIM_TYPES = {
    'w': os.getenv('CLAIM_W', '$w'),
    'wa': os.getenv('CLAIM_WA', '$wa'),
    'wx': os.getenv('CLAIM_WX', '$wx'),
    'wg': os.getenv('CLAIM_WG', '$wg'),
    'h': os.getenv('CLAIM_H', '$h'),
    'ha': os.getenv('CLAIM_HA', '$ha'),
    'hg': os.getenv('CLAIM_HG', '$hg'),
    'hx': os.getenv('CLAIM_HX', '$hx'),
    'ma': os.getenv('CLAIM_MA', '$ma'),
    'm': os.getenv('CLAIM_M', '$m'),
    'mg': os.getenv('CLAIM_MG', '$mg'),
    'mx': os.getenv('CLAIM_MX', '$mx'),
}
VALID_CLAIM_TYPES = list(CLAIM_TYPES.keys())

# ── Keep-alive for Render ──────────────────────────────────────────────
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

# ── Regex Patterns ──────────────────────────────────────────────────────
REGEX_PATTERNS = {
    "KEYS": r'(?:🔑|<:(?:chaos)?key:\d+>)\s*\(\*?\*?([\d,.]+)\*?\*?\)',
    "DK_STOCK": r'\**(\d+)\**\s*\$dk\s*(?:available|dispon[ií]ve(?:l|is)|no estoque|disponible|en stock|disponibles?)',
    "DK_POWER": r'(?:power|poder):\s*\*{0,2}(\d+)%\*{0,2}',
    "DK_CONSUMPTION": r'(?:each kakera (?:reaction|button) consumes|cada (?:reação|botão|botón) de kakera consume|chaque bouton kakera consomme)\s*(\d+)%',
    "DK_READY": r'\$dk.*?(?:ready|pronto|disponible|prêt|dispon[ií]vel|listo)',
}

# ── Bot ──────────────────────────────────────────────────────────────
class MudaeRoller(discord.Client):
    def __init__(self):
        super().__init__()
        self.is_running = False
        self.roll_count = 0          # counts 1→20, drives the $us 20 trigger
        self.total_rolls = 0         # lifetime stat for !status only
        self.channel = None
        self.last_send_time = 0
        self.stop_flag = False
        self.rate_limited_until = 0
        self.rate_limit_retries = 0

        # Serializes ALL outgoing actions (sends + button clicks) through one
        # shared rate-limit gate, so nothing can race and cause a 429 storm.
        self.send_lock = asyncio.Lock()

        # ── KAKERA ──────────────────────────────────────────────────────
        self.kakera_enabled = True
        self.only_chaos = True  # ONLY react to 10+ keys characters
        self.dk_stock_count = 0
        self.current_dk_power = 100
        self.dk_consumption = 35
        self.dk_consumption_chaos = 18
        self.max_dk_power = 100
        self.last_dk_power_update_utc = None
        self.kakera_reaction_cooldown = 0
        self.kakera_reacted_messages = set()

        # ── KAKERA EMOJIS ──────────────────────────────────────────────
        self.kakera_emojis = ['kakeraY', 'kakeraO', 'kakeraR', 'kakeraW', 'kakeraL', 'kakeraP', 'kakeraD', 'kakeraC']
        self.chaos_emojis = ['kakeraY', 'kakeraO', 'kakeraR', 'kakeraW', 'kakeraL', 'kakeraP', 'kakeraD', 'kakeraC']
        self.sphere_emojis = ['spP', 'spB', 'spT', 'spG', 'spY', 'spO', 'spR', 'spW', 'spL', 'spD', 'spM', 'spP2', 'spB2', 'spT2', 'spG2', 'spY2', 'spO2', 'spR2', 'spW2', 'spL2', 'spD2', 'spU']

        # DESIRED KAKERA FOR 10+ KEYS: Purple(P), Light(L), Red(R), Chaos(C), Rainbow(W)
        self.desired_kakera = ['kakeraP', 'kakeraL', 'kakeraR', 'kakeraC', 'kakeraW']
        self.kakera_priority_order = ['kakeraP', 'kakeraL', 'kakeraR', 'kakeraC', 'kakeraW', 'kakeraO', 'kakeraY', 'kakeraD']

        # ── Current claim ──────────────────────────────────────────────
        self.current_claim = '$wa'
        self.current_claim_type = 'wa'

        # ── Owner / lifecycle ────────────────────────────────────────────
        self.owner_id = None
        self._ready = False
        self._has_initialized = False   # prevents duplicate setup on reconnect
        self.roll_loop_task = None

    async def on_ready(self):
        # ── Reconnect guard ──────────────────────────────────────────────
        # discord.py-self calls on_ready again after every reconnect.
        # Without this guard, each reconnect spawned a brand new roll_loop
        # task on top of the old one — multiple loops rolling in parallel
        # is what caused the rate-limit storms and eventual crashes.
        if self._has_initialized:
            logger.info(f'🔄 Reconnected as {self.user.name}. Roll loop already running — no action needed.')
            self.channel = self.get_channel(CHANNEL_ID) or self.channel
            if self.roll_loop_task is None or self.roll_loop_task.done():
                logger.warning('Roll loop task was not running — restarting it.')
                self.is_running = True
                self.stop_flag = False
                self.roll_loop_task = asyncio.create_task(self.roll_loop())
            return

        logger.info(f'Logged in as {self.user.name} (ID: {self.user.id})')
        self.owner_id = self.user.id

        try:
            self.channel = self.get_channel(CHANNEL_ID)
            if not self.channel:
                self.channel = await self.fetch_channel(CHANNEL_ID)
            if not self.channel:
                logger.error(f'Channel {CHANNEL_ID} not found!')
                return
            logger.info(f'Connected to #{self.channel.name}')
        except Exception as e:
            logger.error(f'Error getting channel: {e}')
            return

        logger.info(f'🎴 ONLY_CHAOS: {"ON (10+ keys only)" if self.only_chaos else "OFF"}')
        logger.info(f'🎴 Desired kakera: {self.desired_kakera}')
        logger.info(f'🚀 Roll delay: {ROLL_DELAY_MIN}-{ROLL_DELAY_MAX}s per roll, $us 20 after every 20 rolls')

        self._has_initialized = True
        self._ready = True
        self.is_running = True

        await self.check_status()
        self.roll_loop_task = asyncio.create_task(self.roll_loop())

    # ── Rate-limit gate (shared by sends AND button clicks) ────────────
    async def _rate_limit_gate(self):
        """Wait out any active cooldown and enforce a minimum gap since the
        last action. Call this before every send() or btn.click()."""
        now = time.time()
        if now < self.rate_limited_until:
            wait = self.rate_limited_until - now
            logger.info(f'⏳ Rate limited, waiting {wait:.1f}s')
            await asyncio.sleep(wait + 0.5)
            self.rate_limited_until = 0

        gap = time.time() - self.last_send_time
        min_gap = 0.9  # leaves headroom in the shared per-account bucket
        if gap < min_gap:
            await asyncio.sleep(min_gap - gap + random.uniform(0.05, 0.15))

    def _register_rate_limit(self, retry_after):
        retry_after = float(retry_after or 5) + random.uniform(0.5, 2.0)
        self.rate_limited_until = time.time() + retry_after
        logger.warning(f'🚫 Rate limited! Pausing {retry_after:.1f}s')

    # ── Safe Send with Rate Limit Handling ────────────────────────────
    async def safe_send(self, msg, retry_count=0):
        if not self.channel or not self._ready:
            return False

        async with self.send_lock:
            await self._rate_limit_gate()
            try:
                await self.channel.send(msg)
                self.last_send_time = time.time()
                self.rate_limit_retries = 0
                return True
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    self._register_rate_limit(getattr(e, 'retry_after', 5))
                    if retry_count < 3:
                        await asyncio.sleep(self.rate_limited_until - time.time() + 0.5)
                        return await self.safe_send(msg, retry_count + 1)
                    return False
                else:
                    logger.error(f'HTTP error: {e}')
                    return False
            except Exception as e:
                logger.error(f'Send error: {e}')
                await asyncio.sleep(2)
                return False

    async def safe_click(self, btn):
        """Click a button through the same shared rate-limit gate as safe_send."""
        async with self.send_lock:
            await self._rate_limit_gate()
            try:
                await btn.click()
                self.last_send_time = time.time()
                return True
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    self._register_rate_limit(getattr(e, 'retry_after', 5))
                return False
            except Exception:
                return False

    # ── Count Chaos Keys ──────────────────────────────────────────────
    def count_chaos_keys(self, embed):
        if not embed or not embed.description:
            return 0
        matches = re.findall(REGEX_PATTERNS["KEYS"], embed.description, re.IGNORECASE)
        return sum(1 for m in matches if int(re.sub(r"[^\d]", "", m)) >= 10)

    # ── Check Status ──────────────────────────────────────────────────
    async def check_status(self):
        if not self.channel or not self._ready:
            return False
        try:
            logger.info('🔍 Checking status with $tu...')
            await self.safe_send('$tu')
            await asyncio.sleep(3)

            async for msg in self.channel.history(limit=10):
                if msg.author.id == MUDAE_BOT_ID and msg.content:
                    content_lower = msg.content.lower()

                    stock_match = re.search(REGEX_PATTERNS["DK_STOCK"], content_lower)
                    if stock_match:
                        self.dk_stock_count = int(stock_match.group(1))
                        logger.info(f'💪 DK Stock: {self.dk_stock_count}')

                    power_match = re.search(REGEX_PATTERNS["DK_POWER"], content_lower)
                    if power_match:
                        self.current_dk_power = int(power_match.group(1))
                        self.last_dk_power_update_utc = datetime.now(timezone.utc)
                        logger.info(f'⚡ DK Power: {self.current_dk_power}%')

                    consumption_match = re.search(REGEX_PATTERNS["DK_CONSUMPTION"], content_lower)
                    if consumption_match:
                        self.dk_consumption = int(consumption_match.group(1))
                        self.dk_consumption_chaos = int(self.dk_consumption / 2)
                        logger.info(f'💎 DK Consumption: Normal={self.dk_consumption}%, Chaos={self.dk_consumption_chaos}%')

                    if re.search(REGEX_PATTERNS["DK_READY"], content_lower):
                        if self.current_dk_power < 50 and self.dk_stock_count > 0:
                            logger.info('💪 Using DK to refill...')
                            await self.safe_send('$dk')
                            self.dk_stock_count -= 1
                    break
            return True
        except Exception as e:
            logger.error(f'Status check error: {e}')
            return False

    # ── Check DK Power ─────────────────────────────────────────────────
    async def check_dk_power(self):
        if not self.dk_stock_count:
            return False

        if self.last_dk_power_update_utc:
            elapsed = (datetime.now(timezone.utc) - self.last_dk_power_update_utc).total_seconds()
            regen = int(elapsed / 180) * 5
            if regen > 0:
                self.current_dk_power = min(self.max_dk_power, self.current_dk_power + regen)
                self.last_dk_power_update_utc = datetime.now(timezone.utc)

        min_power_needed = min(self.dk_consumption, self.dk_consumption_chaos)

        if self.current_dk_power < min_power_needed and self.dk_stock_count > 0:
            logger.info(f'⚡ DK Power low ({self.current_dk_power}%). Using $dk...')
            await self.safe_send('$dk')
            self.dk_stock_count -= 1
            self.current_dk_power = self.max_dk_power
            self.last_dk_power_update_utc = datetime.now(timezone.utc)
            return True
        return False

    # ── GET KAKERA PRIORITY ────────────────────────────────────────────
    def _get_kakera_priority(self, emoji_name):
        emoji_clean = emoji_name.rstrip('2')
        try:
            return len(self.kakera_priority_order) - self.kakera_priority_order.index(emoji_clean)
        except ValueError:
            return 0

    def _is_free_kakera(self, emoji_name):
        emoji_clean = emoji_name.rstrip('2')
        if emoji_clean == 'kakeraP':
            return True
        if emoji_clean in [s.rstrip('2') for s in self.sphere_emojis]:
            return True
        return False

    # ── HANDLE KAKERA REACTIONS ──────────────────────────────────────
    async def handle_kakera_reactions(self, message):
        """ONLY react to 10+ keys characters with: Purple(P), Light(L), Red(R), Chaos(C), Rainbow(W)"""
        try:
            if not self.kakera_enabled:
                return False
            if not message.components:
                return False
            if time.time() < self.kakera_reaction_cooldown:
                return False

            embed = message.embeds[0] if message.embeds else None
            chaos_count = self.count_chaos_keys(embed) if embed else 0

            if self.only_chaos and chaos_count == 0:
                return False

            has_chaos = chaos_count > 0
            char_name = embed.author.name if (embed and embed.author) else "Unknown"

            if has_chaos:
                logger.info(f'🔑 10+ keys: {char_name} ({chaos_count} keys)')

            kakera_buttons = []
            all_kakera = self.kakera_emojis + self.chaos_emojis + self.sphere_emojis

            for row in message.components:
                for btn in row.children:
                    if not hasattr(btn, 'emoji') or not btn.emoji:
                        continue
                    
                    # FIXED: Safe emoji name extraction using getattr
                    emoji_name = getattr(btn.emoji, 'name', None)
                    if not emoji_name:
                        continue

                    emoji_clean = emoji_name.rstrip('2')

                    if not (emoji_name in all_kakera or emoji_clean in all_kakera or 'kakera' in emoji_name.lower()):
                        continue

                    is_purple = (emoji_clean == 'kakeraP')
                    is_desired = emoji_clean in self.desired_kakera

                    if not (is_purple or is_desired):
                        continue

                    is_free = self._is_free_kakera(emoji_name)
                    is_chaos = emoji_clean in self.chaos_emojis

                    kakera_buttons.append({
                        'btn': btn,
                        'emoji': emoji_name,
                        'priority': self._get_kakera_priority(emoji_name),
                        'is_free': is_free,
                        'is_chaos': is_chaos,
                    })

            if not kakera_buttons:
                return False

            kakera_buttons.sort(key=lambda x: (not x['is_free'], -x['priority']))
            logger.info(f'🎯 Clicking kakera on {char_name}: {[b["emoji"] for b in kakera_buttons[:3]]}')

            clicked_count = 0
            for button_info in kakera_buttons[:3]:
                btn = button_info['btn']
                emoji_name = button_info['emoji']
                is_free = button_info['is_free']
                is_chaos = button_info['is_chaos']

                if is_free:
                    cost = 0
                elif has_chaos:
                    cost = self.dk_consumption_chaos if is_chaos else self.dk_consumption
                else:
                    cost = self.dk_consumption

                if not is_free and self.current_dk_power < cost:
                    if self.dk_stock_count > 0:
                        logger.info(f'⚡ Power low ({self.current_dk_power}%), using $dk...')
                        await self.safe_send('$dk')
                        self.dk_stock_count -= 1
                        self.current_dk_power = self.max_dk_power
                        self.last_dk_power_update_utc = datetime.now(timezone.utc)
                    else:
                        continue

                clicked_ok = await self.safe_click(btn)
                if clicked_ok:
                    if not is_free:
                        self.current_dk_power = max(0, self.current_dk_power - cost)
                    self.kakera_reacted_messages.add(message.id)
                    self.kakera_reaction_cooldown = time.time() + random.uniform(1.0, 2.0)
                    clicked_count += 1
                    logger.info(f'✅ Clicked {emoji_name} on {char_name}' +
                               (f' (free)' if is_free else f' (cost: {cost}%, power: {self.current_dk_power}%)'))
                    if clicked_count >= 3:
                        break

            return clicked_count > 0
        except Exception as e:
            logger.error(f'Kakera reaction error: {e}')
            return False

    # ── ROLL LOOP ── simple, single steady pace ──────────────────────
    async def roll_loop(self):
        logger.info(f'🚀 Roll loop started! Using: {self.current_claim}')
        logger.info(f'📊 Steady pace: {ROLL_DELAY_MIN}-{ROLL_DELAY_MAX}s per roll, $us 20 every 20 rolls')

        while self.is_running and not self.stop_flag:
            try:
                if not self._ready or not self.channel:
                    await asyncio.sleep(2)
                    continue

                await self.check_dk_power()
                await self.safe_send(self.current_claim)
                self.roll_count += 1
                self.total_rolls += 1

                if self.total_rolls % 50 == 0:
                    logger.info(f'📊 Rolls: {self.total_rolls} | {self.current_claim_type}')

                # ── $us 20 countdown ────────────────────────────────────
                # roll_count's only job is tracking progress toward 20.
                if self.roll_count >= 20:
                    logger.info('✅ 20 rolls done — sending $us 20...')
                    await asyncio.sleep(random.uniform(0.8, 1.2))
                    await self.safe_send('$us 20')
                    self.roll_count = 0

                    logger.info('$us 20 sent — short pause before resuming')
                    for _ in range(4):
                        if self.stop_flag:
                            break
                        await asyncio.sleep(1)
                    continue

                await asyncio.sleep(random.uniform(ROLL_DELAY_MIN, ROLL_DELAY_MAX))

            except asyncio.CancelledError:
                logger.info('Roll loop cancelled')
                break
            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        self.is_running = False
        logger.info(f'Roll loop stopped. Total rolls: {self.total_rolls}')

    # ── MESSAGE HANDLER ──────────────────────────────────────────────
    async def on_message(self, message):
        try:
            if message.content and message.content.startswith('!'):
                if self.owner_id and message.author.id == self.owner_id and message.channel.id == CHANNEL_ID:
                    await self.process_commands(message)
                return

            if message.channel.id != CHANNEL_ID:
                return
            if message.author.id != MUDAE_BOT_ID:
                return

            if message.content and "Command under maintenance!" in message.content:
                logger.warning('⚠️ Mudae is under maintenance!')
                return

            if message.embeds or message.components:
                if message.components:
                    all_kakera = self.kakera_emojis + self.chaos_emojis + self.sphere_emojis
                    has_kakera = False
                    for comp in message.components:
                        for btn in comp.children:
                            if hasattr(btn, 'emoji') and btn.emoji:
                                emoji_name = getattr(btn.emoji, 'name', None)
                                if emoji_name and (emoji_name in all_kakera or emoji_name.rstrip('2') in all_kakera or 'kakera' in emoji_name.lower()):
                                    has_kakera = True
                                    break
                        if has_kakera:
                            break

                    if has_kakera and message.id not in self.kakera_reacted_messages:
                        await asyncio.sleep(random.uniform(0.2, 0.5))
                        await self.handle_kakera_reactions(message)
                        return
        except Exception as e:
            logger.error(f'on_message error: {e}')

    # ── PROCESS COMMANDS ──────────────────────────────────────────────
    async def process_commands(self, message):
        content = message.content.strip()
        if not content.startswith('!'):
            return

        parts = content.split()
        cmd = parts[0].lower()

        logger.info(f'📝 Command: {cmd}')

        if cmd == '!start':
            if len(parts) < 2:
                await message.channel.send(
                    f'⚠️ Specify type!\n'
                    f'Types: {", ".join(VALID_CLAIM_TYPES)}\n'
                    f'Example: `!start wa`'
                )
                return

            claim_type = parts[1].lower()
            if claim_type not in CLAIM_TYPES:
                await message.channel.send(f'❌ Invalid: {claim_type}')
                return

            if self.is_running:
                await message.channel.send(f'⚠️ Already running `{self.current_claim_type}`')
                return

            self.current_claim = CLAIM_TYPES[claim_type]
            self.current_claim_type = claim_type
            self.stop_flag = False
            self.is_running = True

            if self.roll_loop_task is None or self.roll_loop_task.done():
                self.roll_loop_task = asyncio.create_task(self.roll_loop())

            await message.channel.send(f'▶️ Started `{self.current_claim}` ({claim_type})!')
            return

        if cmd == '!stop':
            logger.info('🛑 STOP command received!')
            self.stop_flag = True
            self.is_running = False

            if self.roll_loop_task and not self.roll_loop_task.done():
                self.roll_loop_task.cancel()
                try:
                    await self.roll_loop_task
                except asyncio.CancelledError:
                    pass
                self.roll_loop_task = None

            await message.channel.send(f'🛑 Stopped! Total rolls: {self.total_rolls}')
            logger.info(f'Stopped. Total rolls: {self.total_rolls}')
            return

        if cmd == '!status':
            await message.channel.send(
                f'**Status:** {"🟢 Running" if self.is_running else "🔴 Stopped"}\n'
                f'**Claim:** `{self.current_claim}` ({self.current_claim_type})\n'
                f'**Rolls:** {self.total_rolls} (toward $us: {self.roll_count}/20)\n'
                f'**Kakera:** {"ON" if self.kakera_enabled else "OFF"}\n'
                f'**ONLY_CHAOS:** {"ON (10+ keys)" if self.only_chaos else "OFF"}\n'
                f'**DK Power:** {self.current_dk_power}%\n'
                f'**DK Stock:** {self.dk_stock_count}'
            )
            return

        if cmd == '!kakera':
            self.kakera_enabled = not self.kakera_enabled
            await message.channel.send(f'🎴 Kakera {"ENABLED" if self.kakera_enabled else "DISABLED"}!')
            return

        if cmd == '!onlychaos':
            self.only_chaos = not self.only_chaos
            await message.channel.send(f'🔑 ONLY_CHAOS: {"ON (10+ keys only)" if self.only_chaos else "OFF (all chars)"}')
            return

        if cmd == '!help':
            await message.channel.send(
                '**Commands:**\n'
                '`!start <type>` - Start rolling\n'
                '  Types: w, wa, wx, wg, h, ha, hg, hx, ma, m, mg, mx\n'
                '`!stop` - Stop\n'
                '`!status` - Status\n'
                '`!kakera` - Toggle kakera\n'
                '`!onlychaos` - Toggle ONLY_CHAOS\n\n'
                f'**Pace:** ~{ROLL_DELAY_MIN}-{ROLL_DELAY_MAX}s per roll → $us 20 every 20 rolls\n'
                '**Kakera (10+ keys):** Purple(P), Light(L), Red(R), Chaos(C), Rainbow(W)\n'
                'Purple = FREE, Chaos = half DK cost'
            )
            return

# ── MAIN ──────────────────────────────────────────────────────────────
def run_forever():
    """Auto-restarts the bot if it crashes, instead of letting the process die."""
    while True:
        try:
            logger.info(f'Starting bot | Channel: {CHANNEL_ID}')
            bot = MudaeRoller()
            bot.run(TOKEN, reconnect=True)
        except Exception as e:
            logger.error(f'Bot crashed: {e}')
        logger.info('Restarting bot in 10 seconds...')
        time.sleep(10)

if __name__ == '__main__':
    threading.Thread(target=run_webserver, daemon=True).start()
    run_forever()
