# 1. Use an official Python runtime as a parent image
# We use 3.10 to match your (venv) environment
FROM python:3.10-slim

# 2. Set the working directory in the container
WORKDIR /app

# 3. Copy just the requirements file first to leverage Docker cache
COPY requirements.txt .

# 4. Install any needed dependencies specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copy the rest of the application source code
# This copies SpaBoii.py, HA_auto_mqtt.py, proto/, API/, etc.
COPY . .

# 6. Define the command to run the application
CMD ["python3", "SpaBoii.py"]
