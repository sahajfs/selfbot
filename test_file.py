from dotenv import load_dotenv
import os

load_dotenv()

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
SERVER_ID = os.getenv('SERVER_ID')

print(f"Token: {'Found' if TOKEN else 'Not Found'}")
print(f"Token value: {TOKEN[:20] if TOKEN else 'None'}...")
print(f"Channel ID: {CHANNEL_ID if CHANNEL_ID else 'Not Found'}")
print(f"Server ID: {SERVER_ID if SERVER_ID else 'Not Found'}")

# Check if .env file exists
if os.path.exists('.env'):
    print("\n✅ .env file found!")
    with open('.env', 'r') as f:
        content = f.read()
        print("Content:")
        print(content)
else:
    print("\n❌ .env file NOT found!")