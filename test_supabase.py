from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_PUBLISHABLE_KEY")

print(f"URL: {url}")
print(f"Key exists: {key is not None}")

try:
    client = create_client(url, key)

    response = client.table("tickets").select("*").limit(1).execute()

    print("✅ Supabase connected successfully!")
    print(f"Response: {response}")

except Exception as e:
    print(f"❌ Error: {e}")
