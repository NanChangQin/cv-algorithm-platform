"""Run with pytest, or with python inside the pinned inference image."""

import tempfile
import unittest
from pathlib import Path

from cv_algorithm_sdk.faster_rcnn import ARCHITECTURE, check_metadata, model_config


class MetadataTests(unittest.TestCase):
    def test_background_and_class_order(self):
        config = model_config(["cat", "dog"], 128, 192)
        self.assertEqual(config["model"]["num_classes"], 3)
        self.assertEqual(config["_dataproc"]["category_map"], {"cat": 1, "dog": 2})
        check_metadata(
            {
                "architecture": ARCHITECTURE,
                "state_dict": {},
                "class_names": ["cat", "dog"],
            },
            config,
        )
        with self.assertRaises(ValueError):
            check_metadata({"state_dict": {}, "class_names": ["dog", "cat"]}, config)
        with self.assertRaises(ValueError):
            check_metadata(
                {"architecture": "fasterrcnn_resnet50_fpn_v2.pt", "state_dict": {}},
                config,
            )
        with self.assertRaises(ValueError):
            model_config(["cat", "cat"])

    def test_class_aware_nms_keeps_different_overlapping_classes(self):
        try:
            import torch
        except ImportError:
            self.skipTest("requires the pinned torch runtime image")
        from faster_rcnn_algorithm.predictor import TorchvisionPredictor
        from PIL import Image

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "input.png"
            Image.new("RGB", (20, 20)).save(path)
            predictor = TorchvisionPredictor(
                root / "config.yaml", root / "model.pth", root, "cpu", False
            )
            predictor._torch = torch
            predictor._labels = {1: "cat", 2: "dog"}
            predictor._model = lambda _: [
                {
                    "boxes": torch.tensor([[0.0, 0.0, 10.0, 10.0]] * 3),
                    "scores": torch.tensor([0.9, 0.8, 0.7]),
                    "labels": torch.tensor([1, 2, 1]),
                }
            ]
            result = predictor.predict(str(path), 0.1, 0.5, 100, 0)
            self.assertEqual([d.label for d in result.detections], ["cat", "dog"])
            self.assertEqual(
                len(predictor.predict(str(path), 0.85, 0.5, 100, 0).detections), 1
            )
            self.assertEqual(
                len(predictor.predict(str(path), 0.1, 0.5, 1, 0).detections), 1
            )


if __name__ == "__main__":
    unittest.main()
