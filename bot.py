import discord
import asyncio
import time
import random
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID', '0'))

class MudaeRoller(discord.Client):
    def __init__(self):
        super().__init__()
        self.is_running = False
        self.roll_count = 0
        self.total_rolls = 0
        self.channel = None
        self.last_time = 0
        self.stop_flag = False
        
    async def on_ready(self):
        print(f'✅ Logged in as {self.user.name}')
        self.channel = self.get_channel(CHANNEL_ID)
        if not self.channel:
            print(f'❌ Channel {CHANNEL_ID} not found!')
            return
        print(f'✅ Connected to: #{self.channel.name}')
        
        # SEND WELCOME
        await self.channel.send("🤖 Bot online! Starting in 3 seconds...")
        await asyncio.sleep(3)
        
        # FORCE START - NO COMMAND NEEDED
        print('🚀 FORCE STARTING ROLLS...')
        self.is_running = True
        self.stop_flag = False
        asyncio.create_task(self.roll_loop())
    
    async def send_msg(self, msg):
        try:
            if time.time() - self.last_time < 1.0:
                await asyncio.sleep(1.0 - (time.time() - self.last_time))
            await self.channel.send(msg)
            self.last_time = time.time()
            return True
        except:
            return False
    
    async def roll_loop(self):
        print('🔄 Rolling started!')
        self.roll_count = 0
        
        while self.is_running and not self.stop_flag:
            try:
                # Send roll
                await self.send_msg('$wg')
                self.roll_count += 1
                self.total_rolls += 1
                
                if self.total_rolls % 100 == 0:
                    print(f'📊 Total rolls: {self.total_rolls}')
                
                # $us after 20 rolls
                if self.roll_count >= 20:
                    print('🔄 Using $us 20...')
                    await asyncio.sleep(0.5)
                    await self.send_msg('$us 20')
                    self.roll_count = 0
                    
                    # 15 SECOND PAUSE
                    print('⏳ Pause 15s - type !stop now!')
                    for i in range(15):
                        if self.stop_flag:
                            break
                        await asyncio.sleep(1)
                    print('▶️ Resuming...')
                
                await asyncio.sleep(1.0)
                
            except Exception as e:
                print(f'❌ Error: {e}')
                await asyncio.sleep(2)
        
        print('🛑 Roll loop ended')
        self.is_running = False
    
    async def on_message(self, message):
        if message.channel.id != CHANNEL_ID:
            return
        if message.author.id == self.user.id:
            return
            
        content = message.content.lower()
        print(f'📩 Command: {content}')
        
        if content == '!stop':
            self.stop_flag = True
            self.is_running = False
            await message.channel.send(f'🛑 Stopped! Total rolls: {self.total_rolls}')
            print('✅ Stopped!')
            
        elif content == '!start':
            if self.is_running:
                await message.channel.send('Already running!')
                return
            self.stop_flag = False
            self.is_running = True
            await message.channel.send('▶️ Starting!')
            asyncio.create_task(self.roll_loop())
            
        elif content == '!status':
            status = '🟢 Running' if self.is_running else '🔴 Stopped'
            await message.channel.send(f'Status: {status}\nTotal: {self.total_rolls}')

bot = MudaeRoller()
bot.run(TOKEN)
