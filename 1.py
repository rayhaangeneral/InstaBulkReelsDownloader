import os
import random

from insta_downloader import build_loader, download_one

print("Script is starting...")

def main():
    input_file = "glitch_mindset_reels.txt"
    output_folder = "reels"
    cookie_file = "cookie.json"  # Define cookie file path

    print("--- PREPARING ---")
    
    # 1. SETUP INSTALOADER (and load cookies if present)
    if os.path.exists(cookie_file):
        print(f"🔑 Loading cookies from {cookie_file}...")
    else:
        print(f"⚠️  {cookie_file} not found. Running anonymously (Higher chance of failure).")
    L = build_loader(
        output_folder=output_folder,
        cookie_file=(cookie_file if os.path.exists(cookie_file) else None),
        max_connection_attempts=3,
        request_timeout=60,
    )

    # 2. READ FILE
    if not os.path.exists(input_file):
        print(f"❌ Error: {input_file} not found!")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    total = len(urls)
    print(f"📊 Found {total} reels to download")
    print("="*50)

    # 3. DOWNLOAD LOOP
    successful = 0
    failed = 0

    for i, url in enumerate(urls, 1):
        print(f"\n[{i}/{total}] Processing: {url}")
        
        res = download_one(L, url=url, output_folder=output_folder)
        if res.ok:
            successful += 1
        else:
            failed += 1

        # Wait before next download (Randomized to look human)
        if i < total:
            sleep_time = random.randint(255, 1005)
            print(f"⏳ Sleeping {sleep_time}s...")
            __import__("time").sleep(sleep_time)

    print("\n" + "="*50)
    print(f"✅ Successful: {successful}")
    print(f"❌ Failed: {failed}")
    print("="*50)

if __name__ == "__main__":
    main()
    input("\nPress Enter to exit...")