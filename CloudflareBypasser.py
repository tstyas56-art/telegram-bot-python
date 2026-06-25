import asyncio
from DrissionPage import ChromiumPage


class CloudflareBypasser:
    def __init__(self, driver: ChromiumPage, max_retries=-1, log=True):
        self.driver = driver
        self.max_retries = max_retries
        self.log = log

    # -------------------------
    # Recursive DOM search helpers (still sync - unavoidable with DrissionPage)
    # -------------------------

    def search_recursively_shadow_root_with_iframe(self, ele):
        if ele.shadow_root:
            if ele.shadow_root.child().tag == "iframe":
                return ele.shadow_root.child()
        else:
            for child in ele.children():
                result = self.search_recursively_shadow_root_with_iframe(child)
                if result:
                    return result
        return None

    def search_recursively_shadow_root_with_cf_input(self, ele):
        if ele.shadow_root:
            if ele.shadow_root.ele("tag:input"):
                return ele.shadow_root.ele("tag:input")
        else:
            for child in ele.children():
                result = self.search_recursively_shadow_root_with_cf_input(child)
                if result:
                    return result
        return None

    # -------------------------
    # Button locating logic
    # -------------------------

    def locate_cf_button(self):
        button = None
        eles = self.driver.eles("tag:input")

        for ele in eles:
            if "name" in ele.attrs and "type" in ele.attrs:
                if "turnstile" in ele.attrs["name"] and ele.attrs["type"] == "hidden":
                    button = ele.parent().shadow_root.child()("tag:body").shadow_root("tag:input")
                    break

        if button:
            return button

        self.log_message("Basic search failed. Searching recursively...")

        ele = self.driver.ele("tag:body")
        iframe = self.search_recursively_shadow_root_with_iframe(ele)

        if iframe:
            return self.search_recursively_shadow_root_with_cf_input(iframe("tag:body"))

        self.log_message("Iframe not found. Button search failed.")
        return None

    # -------------------------
    # Utilities
    # -------------------------

    def log_message(self, message):
        if self.log:
            print(message)

    def click_verification_button(self):
        try:
            button = self.locate_cf_button()
            if button:
                self.log_message("Verification button found. Clicking...")
                button.click()
            else:
                self.log_message("Verification button not found.")
        except Exception as e:
            self.log_message(f"Error clicking verification button: {e}")

    def is_bypassed(self):
        try:
            return "just a moment" not in self.driver.title.lower()
        except Exception as e:
            self.log_message(f"Error checking title: {e}")
            return False

    # -------------------------
    # Async wrapper for blocking loop
    # -------------------------

    async def bypass(self):
        """
        Async-friendly bypass loop (does NOT block event loop)
        """
        try_count = 0

        while not self.is_bypassed():

            if 0 < self.max_retries + 1 <= try_count:
                self.log_message("Exceeded maximum retries. Bypass failed.")
                break

            self.log_message(
                f"Attempt {try_count + 1}: Cloudflare detected. Trying bypass..."
            )

            self.click_verification_button()

            try_count += 1

            # IMPORTANT: prevent blocking event loop
            await asyncio.sleep(2)

        if self.is_bypassed():
            self.log_message("Bypass successful.")
        else:
            self.log_message("Bypass failed.")