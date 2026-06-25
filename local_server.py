from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import sys
import os

# تأكد أن ملفات المكتبة في نفس المجلد أو مضمنة في PYTHONPATH
from api import DeepSeekAPI
from pow import DeepSeekPOW
import asyncio
import json
import traceback
import uuid
from pathlib import Path

app = FastAPI()

# السماح لـ React Native بالاتصال (أي مصدر)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- التهيئة ----------
TOKEN = os.environ.get("DEEPSEEK_TOKEN") or os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DEEPSEEK_TOKEN environment variable")

client = DeepSeekAPI(TOKEN)
pow_solver = DeepSeekPOW()

# ---------- مسار رفع ملف ----------
@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    temp_path = None
    try:
        # حفظ الملف مؤقتًا باسم آمن لأن api.py تحتاج مسار ملف حقيقي
        safe_name = Path(file.filename or "upload.bin").name
        temp_path = f"/tmp/{uuid.uuid4().hex}_{safe_name}"
        os.makedirs("/tmp", exist_ok=True)

        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="الملف فارغ")

        with open(temp_path, "wb") as f:
            f.write(content)

        print(f"[UPLOAD] استلام ملف: {safe_name} الحجم: {len(content)} bytes المسار المؤقت: {temp_path}", flush=True)

        # رفع الملف عبر المكتبة
        file_id = await client._upload_single_file(temp_path)

        return {"file_id": file_id}

    except HTTPException:
        raise
    except Exception as e:
        error_text = traceback.format_exc()
        print("[UPLOAD ERROR]", error_text, flush=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(e),
                "type": type(e).__name__,
                "hint": "راجع الطرفية؛ تمت طباعة traceback كامل يبدأ بـ [UPLOAD ERROR]",
            },
        )
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# ---------- مسار حل PoW للدردشة (اختياري) ----------
@app.get("/pow")
async def get_pow():
    """يُعيد x-ds-pow-response جاهزًا للدردشة"""
    try:
        challenge = await client._get_pow_challenge()
        pow_response = await pow_solver.solve_challenge(challenge)
        return {"pow_response": pow_response}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

import socket


def find_free_port(start_port=8022, max_tries=100):
    """يعثر على أول منفذ فارغ بدءًا من start_port."""
    for port in range(start_port, start_port + max_tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"لم يتم العثور على منفذ فارغ بين {start_port} و {start_port + max_tries - 1}")


# ---------- تشغيل الخادم ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", find_free_port(8022)))
    print(f"تشغيل الخادم على: http://0.0.0.0:{port}")
    # تعديل السطر ليكون بالتمرير النصي:
    uvicorn.run("local_server:app", host="0.0.0.0", port=port, reload=False)
