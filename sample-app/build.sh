#!/bin/bash
set -e

APP_NAME=${APP_NAME:-hello-app}
REGISTRY_URL="localhost:5001"

echo "Building Enterprise Simulation Platform Dashboard..."
echo "App: $APP_NAME"

# Build Docker image
echo "Building Docker image..."
docker build -t $APP_NAME:latest .

# Tag for k3d registry
echo "Tagging image for k3d registry..."
docker tag $APP_NAME:latest $REGISTRY_URL/$APP_NAME:latest

# Push to k3d registry
echo "Pushing to k3d registry..."
docker push $REGISTRY_URL/$APP_NAME:latest

echo "Build completed: $REGISTRY_URL/$APP_NAME:latest"
echo ""
echo "To run locally:"
echo "  docker run -p 8080:8080 -e APP_NAME=$APP_NAME -e REGION=local -e NAMESPACE=default $APP_NAME:latest"
echo ""
echo "To deploy to cluster:"
echo "  python3 -m enterprise_sim service install sample-app"