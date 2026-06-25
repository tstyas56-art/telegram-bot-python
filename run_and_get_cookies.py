# run_and_get_cookies.py (async minimal version)

import asyncio
import os
import sys
import json
import aiohttp


async def get_and_save_cookies(server_url, cookie_file_path):
    for attempt in range(5):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(server_url) as response:
                    response.raise_for_status()
                    cookies_data = await response.json()

            cookies_to_save = {
                'cookies': cookies_data.get('cookies', {}),
                'user_agent': cookies_data.get('user_agent', '')
            }

            os.makedirs(os.path.dirname(cookie_file_path), exist_ok=True)
            with open(cookie_file_path, 'w', encoding='utf-8') as f:
                json.dump(cookies_to_save, f, indent=4, ensure_ascii=False)

            return

        except aiohttp.ClientConnectionError:
            if attempt < 4:
                await asyncio.sleep(5)
            else:
                raise


async def run_server_background():
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
            cwd=server_dir,
        )
        return process
    except Exception:
        return None


async def main():
    print("Getting the cookies...")

    server_process = await run_server_background()

    if server_process:
        await asyncio.sleep(5)

        server_url = "http://localhost:8021/cookies?url=https://chat.deepseek.com"
        cookie_file = "dsk/dsk/cookies.json"

        await get_and_save_cookies(server_url, cookie_file)

    else:
        print("Failed to start server.")


if __name__ == "__main__":
    asyncio.run(main())