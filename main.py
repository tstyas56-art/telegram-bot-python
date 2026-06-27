from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from deepseek_api import api  # تأكد من أن الملف السابق اسمه deepseek_api.py
import json

TOKEN = "dN6orXZDiniHSinFFTEq6naBGUBExC8R0WL+2K6VFHKDc7dW2PR4tqPhOU4mmPPX"

app = FastAPI()
api = DeepSeekAPI(auth_token=TOKEN)


@app.post("/chat")
async def chat_endpoint(request: Request):
    """مسار الدردشة الرئيسي - يستقبل نفس payload الـ React Native ويعيد SSE"""
    try:
        body = await request.json()
    except Exception:
        return StreamingResponse(
            "data: {\"error\": \"Invalid JSON\"}\n\n",
            media_type="text/event-stream"
        )

    chat_session_id = body.get("chat_session_id")
    prompt = body.get("prompt")
    parent_message_id = body.get("parent_message_id")
    ref_file_ids = body.get("ref_file_ids", [])
    thinking_enabled = body.get("thinking_enabled", True)
    search_enabled = body.get("search_enabled", False)

    # التحقق من الحقول المطلوبة
    if not chat_session_id or not prompt:
        return StreamingResponse(
            "data: {\"error\": \"Missing required fields\"}\n\n",
            media_type="text/event-stream"
        )

    async def event_stream():
        try:
            async for chunk in api.chat_completion(
                chat_session_id=chat_session_id,
                prompt=prompt,
                parent_message_id=parent_message_id,
                ref_file_ids=ref_file_ids,
                thinking_enabled=thinking_enabled,
                search_enabled=search_enabled,
            ):
                yield f"data: {json.dumps(chunk)}\n\n"

            # إرسال إشارة التوقف
            yield f"data: {json.dumps({'type': 'text', 'content': '', 'finish_reason': 'stop'})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.on_event("shutdown")
async def shutdown():
    await api.close()


# لتشغيل السيرفر: uvicorn main:app --host 0.0.0.0 --port 8000