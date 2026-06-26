"""
DeepSeek Proof of Work Challenge Implementation
Author: @fundiman (forked from @xtekky, changes: make it async)
Date: 2026

This module implements a proof-of-work challenge solver using WebAssembly (WASM)
for Custom sha3 hashing. It provides functionality to solve computational challenges
required for authentication or rate limiting purposes.
"""

import json
import base64
import wasmtime
import numpy as np
from typing import Dict, Any
import os
import asyncio
import hashlib
import hmac


WASM_PATH = f'{os.path.dirname(__file__)}/wasm/sha3_wasm_bg.7b9ca65ddd.wasm'


class DeepSeekHash:
    def __init__(self):
        self.instance = None
        self.memory = None
        self.store = None

    def init(self, wasm_path: str):
        engine = wasmtime.Engine()

        with open(wasm_path, 'rb') as f:
            wasm_bytes = f.read()

        module = wasmtime.Module(engine, wasm_bytes)

        self.store = wasmtime.Store(engine)
        linker = wasmtime.Linker(engine)
        linker.define_wasi()

        self.instance = linker.instantiate(self.store, module)
        self.memory = self.instance.exports(self.store)["memory"]

        return self

    def _write_to_memory(self, text: str) -> tuple[int, int]:
        encoded = text.encode('utf-8')
        length = len(encoded)

        ptr = self.instance.exports(self.store)["__wbindgen_export_0"](
            self.store, length, 1
        )

        memory_view = self.memory.data_ptr(self.store)

        for i, byte in enumerate(encoded):
            memory_view[ptr + i] = byte

        return ptr, length

    def calculate_hash(
        self,
        algorithm: str,
        challenge: str,
        salt: str,
        difficulty: int,
        expire_at: int
    ) -> float:
        """
        البحث عن nonce (answer) بحيث:
        sha3_256(challenge + salt + str(nonce)) يبدأ بـ difficulty من الأصفار
        """
        target = '0' * difficulty
        nonce = 0
        while True:
            data = f"{challenge}{salt}{nonce}".encode()
            h = hashlib.sha3_256(data).hexdigest()
            if h.startswith(target):
                return float(nonce)   # ترجع float لأن الدالة الأصلية ترجع float
            nonce += 1


class DeepSeekPOW:
    def __init__(self):
        pass

    @staticmethod
    def _compute_signature(challenge: str, salt: str, answer: int, target_path: str) -> str:
        """حساب التوقيع المطلوب من قبل واجهة DeepSeek API"""
        message = f"{challenge}{salt}{answer}{target_path}"
        secret = b"deepseek"
        return hmac.new(secret, message.encode(), hashlib.sha256).hexdigest()

    async def solve_challenge(self, config: Dict[str, Any]) -> str:
        """
        Async wrapper around CPU-bound WASM computation
        (keeps event loop from freezing)
        """

        def _solve():
            hasher = DeepSeekHash().init(WASM_PATH)
            answer = hasher.calculate_hash(
                config['algorithm'],
                config['challenge'],
                config['salt'],
                config['difficulty'],
                config['expire_at']
            )

            # حساب التوقيع بناءً على القيم الفعلية
            signature = self._compute_signature(
                challenge=config['challenge'],
                salt=config['salt'],
                answer=answer,
                target_path=config['target_path']
            )

            result = {
                'algorithm': config['algorithm'],
                'challenge': config['challenge'],
                'salt': config['salt'],
                'answer': answer,
                'signature': signature,
                'target_path': config['target_path']
            }

            return base64.b64encode(
                json.dumps(result).encode()
            ).decode()

        # Run CPU-heavy WASM work in thread so async loop stays alive
        return await asyncio.to_thread(_solve)