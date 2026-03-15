# Use a minimal python image
FROM python:3.11-alpine

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install dependencies
# --no-cache-dir to minimize image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY aird /app/aird

# Expose the default port
EXPOSE 8000

# Run the application
CMD ["python", "-m", "aird"]
