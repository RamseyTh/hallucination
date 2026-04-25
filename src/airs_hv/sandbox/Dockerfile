# Use an official Python 3.11 slim image as the base.
# 'slim' provides a minimal installation of Python, keeping the image size down.
FROM python:3.11-slim

# Set the working directory for the application.
# The host workspace, containing the generated code and requirements, will be
# mounted to this directory during the dynamic validation stage.
WORKDIR /app

# Update package lists and install build-essential.
# This package group includes compilers (gcc, g++) and other tools necessary
# for building Python packages with C extensions (e.g., numpy, pandas) that
# might be hallucinated by the LLM.
# We clean up the apt cache in the same layer to reduce the final image size.
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

# The command to execute inside the container is provided by the `run_in_sandbox.py`
# script. A default CMD is included here for clarity and to allow for manual
# container runs for debugging purposes.
CMD ["python"]
