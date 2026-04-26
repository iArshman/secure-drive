# ==========================================
# STAGE 1: Build Telegram Bot API
# ==========================================
FROM ubuntu:22.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

# Install dependencies required for building
RUN apt-get update && apt-get install -y \
    make git zlib1g-dev libssl-dev gperf cmake g++

# Clone and build telegram-bot-api
RUN git clone --recursive https://github.com/tdlib/telegram-bot-api.git /usr/src/telegram-bot-api
WORKDIR /usr/src/telegram-bot-api
RUN mkdir build && cd build && \
    cmake -DCMAKE_BUILD_TYPE=Release .. && \
    cmake --build . --target install -j$(nproc)

# ==========================================
# STAGE 2: Run Python Bot + API Server
# ==========================================
FROM python:3.10-slim

WORKDIR /app

# Install runtime dependencies for telegram-bot-api
RUN apt-get update && apt-get install -y \
    libssl3 zlib1g \
    && rm -rf /var/lib/apt/lists/*

# Copy the compiled TG API binary from STAGE 1
COPY --from=builder /usr/local/bin/telegram-bot-api /usr/local/bin/telegram-bot-api

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all your bot files
COPY . .

# IMPORTANT: Make the startup script executable
RUN chmod +x start.sh

# Run the startup script (which starts both API and Bot)
CMD ["./start.sh"]
