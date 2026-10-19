def test_model_present_and_shapes():
    import onnxruntime as ort

    from ptztracker.models import POSE_MODEL_PATH

    s = ort.InferenceSession(str(POSE_MODEL_PATH), providers=["CPUExecutionProvider"])
    assert s.get_inputs()[0].shape == [1, 3, 640, 640]
    assert s.get_outputs()[0].shape == [1, 56, 8400]
