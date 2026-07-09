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

# ── Bot ──────────────────────────────────────────────────────────────────
class MudaeRoller(discord.Client):
    def __init__(self):
        super().__init__()
        self.is_running = False
        self.roll_count = 0
        self.total_rolls = 0
        self.channel = None
        self.last_send_time = 0
        self.stop_flag = False
        self.rate_limited_until = 0
        self.rate_limit_retries = 0

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
        
        # DESIRED KAKERA FOR 10+ KEYS: Purple, Orange, Light, Rainbow, Chaos
        self.desired_kakera = ['kakeraP', 'kakeraO', 'kakeraL', 'kakeraC', 'kakeraY']
        self.kakera_priority_order = ['kakeraP', 'kakeraO', 'kakeraL', 'kakeraC', 'kakeraY', 'kakeraR', 'kakeraW', 'kakeraD', 'kakeraG', 'kakeraT']
        
        # ── Current claim ──────────────────────────────────────────────
        self.current_claim = '$wa'
        self.current_claim_type = 'wa'
        
        # ── Owner ──────────────────────────────────────────────────────
        self.owner_id = None
        self._ready = False
        self.roll_loop_task = None

    async def on_ready(self):
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
        logger.info(f'🚀 Roll speed: Fast with humanized randomization')
        
        self._ready = True
        self.is_running = True
        
        await self.check_status()
        self.roll_loop_task = asyncio.create_task(self.roll_loop())

    # ── Humanized Roll Speed ───────────────────────────────────────────
    def get_humanized_delay(self):
        """Fast but humanized delay to avoid rate limits"""
        # Base fast speed: 0.8 to 1.2 seconds
        base_delay = random.uniform(0.8, 1.2)
        
        # 10% chance of a slightly longer pause (1.5-2.5s)
        if random.random() < 0.10:
            base_delay += random.uniform(0.5, 1.5)
        
        # Add small jitter
        jitter = random.uniform(-0.15, 0.15)
        
        return max(0.5, base_delay + jitter)

    # ── Safe Send with Rate Limit Handling ────────────────────────────
    async def safe_send(self, msg, retry_count=0):
        """Send a message with smart rate limit handling"""
        if not self.channel or not self._ready:
            return False
        
        now = time.time()
        
        if now < self.rate_limited_until:
            wait = self.rate_limited_until - now
            logger.info(f'⏳ Rate limited, waiting {wait:.1f}s')
            await asyncio.sleep(wait + 0.5)
            self.rate_limited_until = 0

        gap = time.time() - self.last_send_time
        min_gap = 0.8
        if gap < min_gap:
            await asyncio.sleep(min_gap - gap + random.uniform(0.1, 0.2))

        try:
            await self.channel.send(msg)
            self.last_send_time = time.time()
            self.rate_limit_retries = 0
            return True
            
        except discord.errors.HTTPException as e:
            if e.status == 429:
                retry_after = float(getattr(e, 'retry_after', 5) or 5)
                retry_after += random.uniform(0.5, 2.0)
                logger.warning(f'🚫 Rate limited! Pausing {retry_after:.1f}s')
                self.rate_limited_until = time.time() + retry_after
                
                if retry_count < 3:
                    await asyncio.sleep(retry_after + 0.5)
                    return await self.safe_send(msg, retry_count + 1)
                return False
            else:
                logger.error(f'HTTP error: {e}')
                return False
                
        except Exception as e:
            logger.error(f'Send error: {e}')
            await asyncio.sleep(2)
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
                            await asyncio.sleep(2)
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
            await asyncio.sleep(2)
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
        """ONLY react to 10+ keys characters with: Purple, Orange, Light, Rainbow, Chaos"""
        if not self.kakera_enabled:
            return False

        if not message.components:
            return False

        if time.time() < self.kakera_reaction_cooldown:
            return False

        embed = message.embeds[0] if message.embeds else None
        chaos_count = self.count_chaos_keys(embed) if embed else 0
        
        # ONLY_CHAOS: Skip characters WITHOUT 10+ keys
        if self.only_chaos and chaos_count == 0:
            return False

        has_chaos = chaos_count > 0
        char_name = embed.author.name if (embed and embed.author) else "Unknown"
        
        if has_chaos:
            logger.info(f'🔑 10+ keys: {char_name} ({chaos_count} keys)')

        # Find kakera buttons - ONLY desired ones
        kakera_buttons = []
        all_kakera = self.kakera_emojis + self.chaos_emojis + self.sphere_emojis
        
        for row in message.components:
            for btn in row.children:
                if not hasattr(btn, 'emoji') or not btn.emoji:
                    continue
                if not hasattr(btn.emoji, 'name') or not btn.emoji.name:
                    continue
                
                emoji_name = btn.emoji.name
                emoji_clean = emoji_name.rstrip('2')
                
                # Check if it's a kakera button
                if not (emoji_name in all_kakera or emoji_clean in all_kakera or 'kakera' in emoji_name.lower()):
                    continue
                
                # ONLY react to desired kakera: Purple, Orange, Light, Rainbow, Chaos
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

        # Sort by priority (free first, then priority)
        kakera_buttons.sort(key=lambda x: (not x['is_free'], -x['priority']))
        
        logger.info(f'🎯 Clicking kakera on {char_name}: {[b["emoji"] for b in kakera_buttons[:3]]}')

        # Click up to 3 buttons
        clicked_count = 0
        for button_info in kakera_buttons[:3]:
            btn = button_info['btn']
            emoji_name = button_info['emoji']
            is_free = button_info['is_free']
            is_chaos = button_info['is_chaos']
            
            # Calculate cost
            if is_free:
                cost = 0
            elif has_chaos:
                cost = self.dk_consumption_chaos if is_chaos else self.dk_consumption
            else:
                cost = self.dk_consumption
            
            # Check power
            if not is_free and self.current_dk_power < cost:
                if self.dk_stock_count > 0:
                    logger.info(f'⚡ Power low ({self.current_dk_power}%), using $dk...')
                    await self.safe_send('$dk')
                    self.dk_stock_count -= 1
                    self.current_dk_power = self.max_dk_power
                    self.last_dk_power_update_utc = datetime.now(timezone.utc)
                    await asyncio.sleep(2)
                else:
                    continue

            try:
                await asyncio.sleep(random.uniform(0.2, 0.5))
                await btn.click()
                
                if not is_free:
                    self.current_dk_power = max(0, self.current_dk_power - cost)
                
                self.kakera_reacted_messages.add(message.id)
                self.kakera_reaction_cooldown = time.time() + random.uniform(1.5, 3.0)
                clicked_count += 1
                
                logger.info(f'✅ Clicked {emoji_name} on {char_name}' + 
                           (f' (free)' if is_free else f' (cost: {cost}%, power: {self.current_dk_power}%)'))
                
                if clicked_count >= 3:
                    break
                    
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    retry_after = float(getattr(e, 'retry_after', 5) or 5)
                    self.kakera_reaction_cooldown = time.time() + retry_after
                continue
            except Exception as e:
                continue

        return clicked_count > 0

    # ── ROLL LOOP ──────────────────────────────────────────────────────
    async def roll_loop(self):
        logger.info(f'🚀 Roll loop started! Using: {self.current_claim}')
        self.roll_count = 0

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

                # $us after 20 rolls
                if self.roll_count >= 20:
                    await asyncio.sleep(random.uniform(0.8, 1.5))
                    await self.safe_send('$us 20')
                    self.roll_count = 0
                    logger.info('$us 20 sent — 5 second pause')
                    
                    # 5 SECOND PAUSE (reduced from 15)
                    for i in range(5):
                        if self.stop_flag:
                            break
                        await asyncio.sleep(1)
                    continue

                # Humanized delay between rolls
                delay = self.get_humanized_delay()
                await asyncio.sleep(delay)

            except asyncio.CancelledError:
                logger.info('Roll loop cancelled')
                break
            except Exception as e:
                logger.error(f'Roll loop error: {e}')
                await asyncio.sleep(5)

        self.is_running = False
        logger.info('Roll loop stopped')

         # ── MESSAGE HANDLER ──────────────────────────────────────────────
    async def on_message(self, message):
        # Process user commands
        if message.content and message.content.startswith('!'):
            if self.owner_id and message.author.id == self.owner_id and message.channel.id == CHANNEL_ID:
                await self.process_commands(message)
            return

        # Skip if not in target channel
        if message.channel.id != CHANNEL_ID:
            return

        # Only process Mudae messages
        if message.author.id != MUDAE_BOT_ID:
            return

        if message.content and "Command under maintenance!" in message.content:
            logger.warning('⚠️ Mudae is under maintenance!')
            return

        # ── KAKERA REACTIONS ──────────────────────────────────────────
        if message.embeds or message.components:
            embed = message.embeds[0] if message.embeds else None
            
            # Check if it's a kakera message
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

    # ── PROCESS COMMANDS ──────────────────────────────────────────────
    async def process_commands(self, message):
        content = message.content.strip()
        if not content.startswith('!'):
            return

        parts = content.split()
        cmd = parts[0].lower()

        logger.info(f'📝 Command: {cmd}')

        # ── START ──────────────────────────────────────────────────────
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

        # ── STOP ────────────────────────────────────────────────────────
        if cmd == '!stop':
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
            return

        # ── STATUS ──────────────────────────────────────────────────────
        if cmd == '!status':
            await message.channel.send(
                f'**Status:** {"🟢 Running" if self.is_running else "🔴 Stopped"}\n'
                f'**Claim:** `{self.current_claim}` ({self.current_claim_type})\n'
                f'**Rolls:** {self.total_rolls} (batch {self.roll_count}/20)\n'
                f'**Kakera:** {"ON" if self.kakera_enabled else "OFF"}\n'
                f'**ONLY_CHAOS:** {"ON (10+ keys)" if self.only_chaos else "OFF"}\n'
                f'**DK Power:** {self.current_dk_power}%\n'
                f'**DK Stock:** {self.dk_stock_count}'
            )
            return

        # ── KAKERA TOGGLE ──────────────────────────────────────────────
        if cmd == '!kakera':
            self.kakera_enabled = not self.kakera_enabled
            await message.channel.send(f'🎴 Kakera {"ENABLED" if self.kakera_enabled else "DISABLED"}!')
            return

        # ── ONLY_CHAOS TOGGLE ──────────────────────────────────────────
        if cmd == '!onlychaos':
            self.only_chaos = not self.only_chaos
            await message.channel.send(f'🔑 ONLY_CHAOS: {"ON (10+ keys only)" if self.only_chaos else "OFF (all chars)"}')
            return

        # ── HELP ────────────────────────────────────────────────────────
        if cmd == '!help':
            await message.channel.send(
                '**Commands:**\n'
                '`!start <type>` - Start rolling\n'
                '  Types: w, wa, wx, wg, h, ha, hg, hx, ma, m, mg, mx\n'
                '`!stop` - Stop\n'
                '`!status` - Status\n'
                '`!kakera` - Toggle kakera\n'
                '`!onlychaos` - Toggle ONLY_CHAOS\n\n'
                '**Kakera (10+ keys):** Purple, Orange, Light, Rainbow, Chaos\n'
                'Purple = FREE, Chaos = half DK cost'
            )
            return

# ── MAIN ──────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    threading.Thread(target=run_webserver, daemon=True).start()
    logger.info(f'Starting bot | Channel: {CHANNEL_ID}')
    MudaeRoller().run(TOKEN)
