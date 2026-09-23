import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

def on_pose (result, frame, timestamp):
    print("pose received")

print("initializing mediapipe...")

base_options = python.BaseOptions(
    delegate=python.BaseOptions.Delegate.GPU
)

options = vision.PoseLandmarkerOptions(
    base_options=base_options,
    running_mode=vision.RunningMode.LIVE_STREAM,
    result_callback=on_pose
)

landmarker = vision.PoseLandmarker.create_from_options(options)

print("retrieving a video frame...")

cap = cv2.VideoCapture(0)
ok, frame = cap.read()

print("converting image...")

mp_frame = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)

print("pose detection...")

landmarker.detect_async(mp_frame, 0)