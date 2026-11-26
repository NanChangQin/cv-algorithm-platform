# Faster R-CNN official initialization

Place `fasterrcnn_resnet50_fpn.pt` here before building the trainer image. The binary is ignored by Git.

Source: https://download.pytorch.org/models/fasterrcnn_resnet50_fpn_coco-258fb6c6.pth

This is torchvision Faster R-CNN ResNet50-FPN COCO_V1 (not the V2 architecture). The Dockerfile verifies SHA256 prefix `258fb6c6`. Training remaps COCO predictor rows by class name while preserving background index 0. The runtime explicitly disables implicit downloads.

See `docs/TRAINING.md` for build order and supported inputs. Training and inference use the same torch 2.10.0 / torchvision 0.25.0 model builder.
