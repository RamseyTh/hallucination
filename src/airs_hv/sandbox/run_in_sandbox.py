"""
Executes a Python script within an isolated Docker container.

This module provides the runtime environment for the dynamic analysis stage,
where generated code is executed to observe its behavior and capture any
runtime errors, which may indicate hallucinations.
"""

import time
from pathlib import Path
from typing import NamedTuple

import docker
from docker.errors import ContainerError, ImageNotFound
from loguru import logger

# The sandbox image name is defined here as this is the only module that uses it.
# This avoids a dependency on a central config module for a local constant.
SANDBOX_IMAGE_NAME = "airs-hv-sandbox:dev"


class ExecutionResult(NamedTuple):
    """
    Represents the result of a code execution in an isolated environment.
    """

    exit_code: int
    """The exit code of the executed script. 0 for success, non-zero for failure."""

    stdout: str
    """The standard output captured from the script."""

    stderr: str
    """The standard error captured from the script."""

    duration: float
    """The total execution time in seconds."""


def _get_docker_client() -> docker.DockerClient:
    """
    Initializes and returns a Docker client, checking for the execution environment image.

    Raises:
        ImageNotFound: If the required Docker image for execution does not exist.
        docker.errors.DockerException: If the Docker daemon is not running or accessible.
    """
    client = docker.from_env()
    try:
        client.images.get(SANDBOX_IMAGE_NAME)
        logger.debug(f"Execution environment image '{SANDBOX_IMAGE_NAME}' found.")
        return client
    except ImageNotFound:
        logger.error(
            f"The required Docker image '{SANDBOX_IMAGE_NAME}' was not found. "
            "Please build it using the provided Dockerfile before running the pipeline."
        )
        raise


def run_in_sandbox(workspace_dir: Path) -> ExecutionResult:
    """
    Runs the main.py script from a workspace directory in a Docker container.

    This function mounts the workspace directory into a container and executes
    the script, capturing its output and exit code. It handles dependency
    installation from a `requirements.txt` file if present.

    Args:
        workspace_dir: The absolute path to the workspace directory containing
                       `main.py` and optionally `requirements.txt`.

    Returns:
        An ExecutionResult object with the execution details.
    """
    start_time = time.monotonic()

    if not (workspace_dir / "main.py").exists():
        return ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr="Error: main.py not found in the workspace.",
            duration=time.monotonic() - start_time,
        )

    try:
        client = _get_docker_client()
    except (ImageNotFound, docker.errors.DockerException) as e:
        return ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr=f"Docker environment error: {e}",
            duration=time.monotonic() - start_time,
        )

    # This command first checks for requirements.txt and installs packages if it
    # exists, then executes the main Python script.
    command = [
        "/bin/sh",
        "-c",
        "if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi && python main.py",
    ]

    container = None
    try:
        container = client.containers.run(
            image=SANDBOX_IMAGE_NAME,
            command=command,
            volumes={str(workspace_dir.resolve()): {"bind": "/app", "mode": "rw"}},
            working_dir="/app",
            detach=True,
            mem_limit="512m",
            # Using cpus=1.0 is more direct than cpu_shares
            nano_cpus=int(1.0 * 1e9),  # Limit to 1 CPU core
        )

        # Wait for the container to finish, with a 60-second timeout.
        result = container.wait(timeout=60)
        exit_code = result.get("StatusCode", -1)

        stdout = container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = container.logs(stdout=False, stderr=True).decode("utf-8", "replace")

    except ContainerError as e:
        # This error is raised if the process inside the container exits non-zero.
        exit_code = e.exit_status
        stdout = e.container.logs(stdout=True, stderr=False).decode("utf-8", "replace")
        stderr = e.container.logs(stdout=False, stderr=True).decode("utf-8", "replace")
        if str(e) not in stderr:
            stderr += f"\nContainerError: {e}"

    except Exception as e:
        # Catches other errors, like a timeout from container.wait()
        return ExecutionResult(
            exit_code=-1,
            stdout="",
            stderr=f"Runtime execution failed: {type(e).__name__}: {e}",
            duration=time.monotonic() - start_time,
        )
    finally:
        if container:
            try:
                container.remove(force=True)
            except docker.errors.APIError as e:
                logger.warning(f"Could not remove container {container.id}: {e}")

    duration = time.monotonic() - start_time
    logger.debug(
        f"Runtime execution for '{workspace_dir.name}' finished in {duration:.2f}s "
        f"with exit code {exit_code}."
    )

    return ExecutionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
    )
