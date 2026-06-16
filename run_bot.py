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
    print("Launching Uvicorn server on port 8000...")
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
