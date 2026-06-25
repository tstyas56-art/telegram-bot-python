import json
import re
import os
import asyncio
from urllib.parse import urlparse

from CloudflareBypasser import CloudflareBypasser
from DrissionPage import ChromiumPage, ChromiumOptions
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel
from typing import Dict

import argparse
from pyvirtualdisplay import Display
import uvicorn
import atexit
import time
from asyncio import to_thread


DOCKER_MODE = os.getenv("DOCKERMODE", "false").lower() == "true"
SERVER_PORT = int(os.getenv("SERVER_PORT", 8021))

browser_path = "/usr/bin/google-chrome"
app = FastAPI()


class CookieResponse(BaseModel):
    cookies: Dict[str, str]
    user_agent: str


def is_safe_url(url: str) -> bool:
    parsed_url = urlparse(url)
    ip_pattern = re.compile(
        r"^(127\.0\.0\.1|localhost|0\.0\.0\.0|::1|10\.\d+\.\d+\.\d+|"
        r"172\.1[6-9]\.\d+\.\d+|172\.2[0-9]\.\d+\.\d+|"
        r"172\.3[0-1]\.\d+\.\d+|192\.168\.\d+\.\d+)$"
    )
    hostname = parsed_url.hostname

    if (hostname and ip_pattern.match(hostname)) or parsed_url.scheme == "file":
        return False
    return True


def verify_page_loaded(driver: ChromiumPage) -> bool:
    try:
        body = driver.ele('tag:body', timeout=10)
        return len(body.html) > 100
    except:
        return False


def bypass_cloudflare(url: str, retries: int, log: bool, proxy: str = None) -> ChromiumPage:
    max_load_retries = 3

    for load_attempt in range(max_load_retries):

        options = ChromiumOptions().auto_port()

        if DOCKER_MODE:
            options.set_argument("--auto-open-devtools-for-tabs", "true")
            options.set_argument("--remote-debugging-port=9222")
            options.set_argument("--no-sandbox")
            options.set_argument("--disable-gpu")
            options.set_paths(browser_path=browser_path).headless(False)
        else:
            options.set_paths(browser_path=browser_path).headless(False)

        if proxy:
            options.set_proxy(proxy)

        driver = ChromiumPage(addr_or_opts=options)

        try:
            driver.get(url)
            time.sleep(5)

            if not verify_page_loaded(driver):
                driver.quit()
                if load_attempt < max_load_retries - 1:
                    time.sleep(3)
                    continue
                raise Exception("Page load failed")

            cf_bypasser = CloudflareBypasser(driver, retries, log)
            cf_bypasser.bypass()

            return driver

        except Exception as e:
            driver.quit()
            if load_attempt < max_load_retries - 1:
                time.sleep(3)
                continue
            raise e


# -------------------------
# Async wrappers for FastAPI
# -------------------------

@app.get("/cookies", response_model=CookieResponse)
async def get_cookies(url: str, retries: int = 5, proxy: str = None):

    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        driver = await to_thread(bypass_cloudflare, url, retries, True, proxy)

        cookies = {
            c.get("name", ""): c.get("value", "")
            for c in driver.cookies()
        }

        user_agent = driver.user_agent

        driver.quit()

        return CookieResponse(cookies=cookies, user_agent=user_agent)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/html")
async def get_html(url: str, retries: int = 5, proxy: str = None):

    if not is_safe_url(url):
        raise HTTPException(status_code=400, detail="Invalid URL")

    try:
        driver = await to_thread(bypass_cloudflare, url, retries, True, proxy)

        html = driver.html

        cookies_json = {
            c.get("name", ""): c.get("value", "")
            for c in driver.cookies()
        }

        response = Response(content=html, media_type="text/html")
        response.headers["cookies"] = json.dumps(cookies_json)
        response.headers["user_agent"] = driver.user_agent

        driver.quit()

        return response

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------
# Main entry  (async-safe sleep)
# -------------------------

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Cloudflare bypass api")
    parser.add_argument("--nolog", action="store_true")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    display = None

    if args.headless or DOCKER_MODE:
        display = Display(visible=0, size=(1920, 1080))
        display.start()

        def cleanup():
            if display:
                display.stop()

        atexit.register(cleanup)

    log = not args.nolog

    uvicorn.run(app, host="0.0.0.0", port=SERVER_PORT)