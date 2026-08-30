import subprocess
import json
from typing import Dict, Optional

class DockerPackageManager:
    def __init__(self):
        pass

    def _get_image_name(self, repo_url: str) -> str:
        """Generates a docker image name based on the github repo url."""
        parts = repo_url.rstrip('/').split('/')
        if len(parts) >= 2:
            return f"mcp_{parts[-2].lower()}_{parts[-1].lower()}"
        return "mcp_unknown"

    def is_image_available(self, image_name: str) -> bool:
        """Checks if a docker image exists locally."""
        try:
            result = subprocess.run(
                ["docker", "images", "-q", image_name],
                capture_output=True, text=True, check=True
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False
        except FileNotFoundError:
            # Docker is not installed or not in PATH
            print("[Warning] Docker CLI not found.")
            return False

    def build_or_pull(self, repo_url: str) -> str:
        """Clones the repo and builds the image, or pulls it if available."""
        image_name = self._get_image_name(repo_url)
        print(f"[DockerPackageManager] Preparing image {image_name} for {repo_url}...")
        
        # We wrap in a mock for now, but in reality this would:
        # 1. git clone repo_url /tmp/dir
        # 2. subprocess.run(["docker", "build", "-t", image_name, "/tmp/dir"])
        print(f"[DockerPackageManager] Successfully provisioned {image_name}.")
        return image_name

    def start_container(self, image_name: str, env_vars: Optional[Dict[str, str]] = None) -> str:
        """Starts the container in detached mode and returns the container ID."""
        print(f"[DockerPackageManager] Starting container from {image_name}...")
        # Mock command: docker run -d --rm image_name
        container_id = "mock_container_id_12345"
        print(f"[DockerPackageManager] Container started with ID {container_id}")
        return container_id

    def stop_container(self, container_id: str):
        """Stops a running container."""
        print(f"[DockerPackageManager] Stopping container {container_id}...")
        # subprocess.run(["docker", "stop", container_id])

    def check_status(self, container_id: str) -> str:
        """Checks if the container is still running."""
        # subprocess.run(["docker", "inspect", "-f", "{{.State.Status}}", container_id])
        return "running"

    def uninstall_mcp_server(self, server_info: dict):
        """Simulates uninstalling or deleting an MCP server from the system."""
        cmd = server_info.get("command", "")
        pkg_name = ""
        if server_info.get("args"):
            pkg_name = server_info["args"][-1]
        
        print(f"[DockerPackageManager] Deleting not-working MCP server: {server_info.get('name', pkg_name)}")
        if "npx" in cmd.lower():
            print(f"[DockerPackageManager] Clearing npx cache for {pkg_name}...")
            # subprocess.run(["npx", "clear-npx-cache"])
        elif "uvx" in cmd.lower() or "pip" in cmd.lower():
            print(f"[DockerPackageManager] Uninstalling python package {pkg_name} from cache...")
        
        print(f"[DockerPackageManager] Server {server_info.get('name')} successfully deleted from the system.")

if __name__ == "__main__":
    manager = DockerPackageManager()
    img = manager.build_or_pull("https://github.com/author/some-mcp-server")
    cid = manager.start_container(img)
    print(f"Container status: {manager.check_status(cid)}")
    manager.stop_container(cid)
