# Multi-stage build for React + Flask application
# Stage 1: Build React frontend
FROM node:18-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm install --omit=dev

COPY frontend/ ./
RUN npm run build

# Stage 2: Production Flask app with React build
FROM python:3.11-slim

# Security: Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Install dependencies
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application code
COPY backend/ ./

# Copy React build from previous stage
COPY --from=frontend-build /app/frontend/build ./frontend/build

# Create data directory for storage
RUN mkdir -p /app/data && chown -R appuser:appuser /app
USER appuser

# Expose HTTP port (8080)
EXPOSE 8080

# Health check using the new API
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8080/api/health')" || exit 1

# Start application
CMD ["python", "app.py"]