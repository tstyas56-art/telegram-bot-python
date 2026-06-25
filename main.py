import uvicorn
import os

if __name__ == "__main__":
    # يستخدم المتغيّر PORT الذي يحدده Railway
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("local_server:app", host="0.0.0.0", port=port)