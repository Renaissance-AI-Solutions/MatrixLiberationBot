FROM python:3.11-slim

# Install system dependencies required for matrix-nio E2EE (libolm)
RUN apt-get update && apt-get install -y \
    libolm-dev \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directory for SQLite and logs
RUN mkdir -p /app/data

# Run the bot
CMD ["python", "main.py"]
