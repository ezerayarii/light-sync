FROM python:3.12-slim-bookworm

# onnxruntime has no build for Light Sync's Alpine image: that's why this is its own small add-on (Nov 9)
RUN pip install --no-cache-dir onnxruntime numpy pillow

COPY server.py pose.py movenet.onnx /app/
WORKDIR /app
CMD ["python3", "-u", "/app/server.py"]
