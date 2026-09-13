import torch
from app.live_inference import SkyGuardLiveClassifier

MODEL_PATH = "app/models/skyguard_pytorch_multiclass_v2.pt"

print("Loading SkyGuard PyTorch model...")

detector = SkyGuardLiveClassifier(MODEL_PATH)

print("Model loaded successfully.")
print("Classes:", detector.classes)
print("Sequence length:", detector.seq_len)