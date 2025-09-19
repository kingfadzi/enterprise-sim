#!/bin/bash
set -e

APP_NAME=${APP_NAME:-hello-app}

echo "Building Enterprise Simulation Platform Dashboard..."
echo "App: $APP_NAME"

# Build Docker image
echo "Building Docker image..."
docker build -t $APP_NAME:latest .

echo "Build completed: $APP_NAME:latest"
echo ""
echo "To run locally:"
echo "  docker run -p 8080:8080 -e APP_NAME=$APP_NAME -e REGION=local -e NAMESPACE=default $APP_NAME:latest"
echo ""
echo "To deploy to cluster:"
echo "  ./enterprise-sim.sh app deploy"