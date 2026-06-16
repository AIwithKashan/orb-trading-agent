import sys
import logging
import uvicorn

# Setup root logger
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger.addHandler(sh)

if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", 8000))
    host = "0.0.0.0" if os.getenv("PORT") else "127.0.0.1"
    print(f"Launching Uvicorn server on {host}:{port}...")
    uvicorn.run("main:app", host=host, port=port, reload=False)
