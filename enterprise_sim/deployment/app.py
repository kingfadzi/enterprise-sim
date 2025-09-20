"""Application image build and deployment management."""

import os
import subprocess
from typing import Dict

class AppImageManager:
    """Manages the application's Docker image build and import process."""

    def __init__(self, app_dir: str = "sample-app"):
        self.app_dir = app_dir
        self.image_name = "enterprise-sim/sample-app:latest"

    def build(self) -> bool:
        """Build the Docker image for the sample application."""
        print(f"Building Docker image: {self.image_name}")
        
        build_script = os.path.join(self.app_dir, "build.sh")
        if not os.path.exists(build_script):
            print(f"ERROR: Build script not found at {build_script}")
            return False

        try:
            env = os.environ.copy()
            env["APP_NAME"] = self.image_name.split(":")[0]
            subprocess.run(
                ["bash", "build.sh"],
                check=True,
                capture_output=True,
                text=True,
                cwd=self.app_dir,
                env=env
            )
            print("✅ Docker image built successfully.")
            return True
        except subprocess.CalledProcessError as e:
            print("❌ ERROR: Docker image build failed.")
            print(f"   STDOUT: {e.stdout}")
            print(f"   STDERR: {e.stderr}")
            return False

    def import_image(self, cluster_name: str) -> bool:
        """Import the Docker image into the k3d cluster."""
        print(f"Importing image {self.image_name} into cluster {cluster_name}...")
        try:
            subprocess.run(
                ["k3d", "image", "import", self.image_name, "-c", cluster_name],
                check=True,
                capture_output=True,
                text=True
            )
            print("✅ Image imported successfully.")
            return True
        except subprocess.CalledProcessError as e:
            print("❌ ERROR: Failed to import image into k3d cluster.")
            print(f"   STDOUT: {e.stdout}")
            print(f"   STDERR: {e.stderr}")
            return False

    def generate_env_file(self, s3_endpoint: str, domain: str) -> bool:
        """Generate the .env file for the sample application."""
        print("Generating .env file for sample-app...")
        
        template_path = os.path.join(self.app_dir, ".env.template")
        env_path = os.path.join(self.app_dir, ".env")

        if not os.path.exists(template_path):
            print(f"ERROR: .env.template not found at {template_path}")
            return False

        try:
            with open(template_path, "r") as f:
                content = f.read()

            # Append required env vars
            content += f"\n\n# Platform-injected variables\n"
            content += f"S3_ENDPOINT_URL=https://{s3_endpoint}\n"
            content += f"DOMAIN={domain}\n"

            with open(env_path, "w") as f:
                f.write(content)
            
            print(f"✅ .env file created at {env_path}")
            return True
        except IOError as e:
            print(f"❌ ERROR: Failed to write .env file: {e}")
            return False
