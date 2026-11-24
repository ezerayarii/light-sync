FROM alpine:3.20

# Python + numpy (beats), Pillow (camera frames), parec (mic through HA's PulseAudio)
# tzdata (Nov 8): without it the clock was UTC - Normal's day, the night hours and the prayer times ran 8 h off
RUN apk add --no-cache python3 py3-numpy py3-requests py3-pillow pulseaudio-utils tzdata

COPY light_sync.py run.py app.py cameras.py checks.py updater.py /app/
COPY parts /app/parts
COPY web /app/web
CMD ["python3", "-u", "/app/run.py"]
