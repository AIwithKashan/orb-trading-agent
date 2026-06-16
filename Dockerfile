FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port 7860 (Hugging Face Spaces default) or read PORT from environment
# We'll use a shell script or python command to read the environment PORT.
# FastAPI run via python run_bot.py which we updated to read PORT environment variable.
# On Hugging Face Spaces, PORT is automatically set to 7860.
# On Render/Koyeb, PORT is also dynamically set.
# So running `python run_bot.py` is perfect!
CMD ["python", "run_bot.py"]
