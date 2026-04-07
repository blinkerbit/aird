# Use a minimal python image
FROM python:3.11-alpine

# Copy Caddy binary from official image
COPY --from=caddy:2-alpine /usr/bin/caddy /usr/bin/caddy

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
# --no-cache-dir to minimize image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY aird /app/aird

# Copy Caddy config and entrypoint
COPY Caddyfile /etc/caddy/Caddyfile
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Setup default mount point and environment
RUN mkdir -p /project_root
ENV AIRD_ROOT="/project_root"

# Expose port 80 (Caddy gateway)
EXPOSE 80

# Run entrypoint: starts aird in background, Caddy in foreground (PID 1)
ENTRYPOINT ["/docker-entrypoint.sh"]
