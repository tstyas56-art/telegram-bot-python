# run_and_get_cookies.py (async version)

import asyncio
import os
import sys
import json
import aiohttp


def validate_cookies(cookies_data):
    """Validate that cf_clearance cookie is present and not empty"""
    cookies = cookies_data.get('cookies', {})
    return 'cf_clearance' in cookies and cookies['cf_clearance'].strip() != ''


async def get_and_save_cookies(server_url, cookie_file_path, max_retries=3):
    """
    Fetch cookies from local server and persist them to disk (async version)
    """
    async with aiohttp.ClientSession() as session:

        for attempt in range(max_retries):
            try:
                async with session.get(server_url) as response:
                    response.raise_for_status()
                    cookies_data = await response.json()

                if not validate_cookies(cookies_data):
                    print(f"Attempt {attempt + 1}: cf_clearance missing, retrying...")
                    await asyncio.sleep(5)
                    continue

                cookies_to_save = {
                    "cookies": cookies_data.get("cookies", {}),
                    "user_agent": cookies_data.get("user_agent", "")
                }

                os.makedirs(os.path.dirname(cookie_file_path), exist_ok=True)
                with open(cookie_file_path, "w", encoding="utf-8") as f:
                    json.dump(cookies_to_save, f, indent=4, ensure_ascii=False)

                print("Successfully obtained and saved cookies with cf_clearance!")
                return True

            except aiohttp.ClientConnectionError as e:
                print(f"Connection error on attempt {attempt + 1}: {e}")

                if attempt < max_retries - 1:
                    await asyncio.sleep(5)
                else:
                    print("Max retries reached. Failed to get valid cookies.")
                    return False

            except Exception as e:
                print(f"Unexpected error: {e}")
                return False

    print("Failed to obtain valid cf_clearance cookie after all attempts")
    return False


async def run_server_background():
    """
    Launch server.py in background async-safe way
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_script = os.path.abspath(os.path.join(script_dir, "server.py"))
    server_dir = os.path.dirname(server_script)

    os.makedirs(server_dir, exist_ok=True)

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            server_script,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=server_dir
        )
        return process
    except Exception:
        return None


async def main():
    print("Getting the cookies...")

    server_process = await run_server_background()

    if server_process:
        await asyncio.sleep(10)

        server_url = "http://localhost:8021/cookies?url=https://chat.deepseek.com"
        cookie_file = "dsk/dsk/cookies.json"

        success = await get_and_save_cookies(server_url, cookie_file, max_retries=5)

        if not success:
            print("Failed to obtain valid cookies.")
            server_process.terminate()
            sys.exit(1)

        server_process.terminate()
    else:
        print("Failed to start server.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())