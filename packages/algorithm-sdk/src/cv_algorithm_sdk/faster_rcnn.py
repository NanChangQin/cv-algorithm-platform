"""The exact torchvision model/preprocessing used by training and inference."""

ARCHITECTURE = "fasterrcnn_resnet50_fpn.pt"


def model_config(class_names, min_size=600, max_size=1000):
    if len(set(class_names)) != len(class_names) or not class_names:
        raise ValueError("类别名称必须非空且唯一")
    return {
        "model": {
            "torchvision_model": "fasterrcnn_resnet50_fpn",
            "num_classes": len(class_names) + 1,
            "min_size": min_size,
            "max_size": max_size,
            "box_score_thresh": 0.0,
            "box_nms_thresh": 1.0,
            "box_detections_per_img": 300,
        },
        "_dataproc": {
            "category_map": {name: i + 1 for i, name in enumerate(class_names)}
        },
        "architecture": ARCHITECTURE,
    }


def build_model(config):
    from torchvision.models.detection import fasterrcnn_resnet50_fpn

    if config.get("torchvision_model") != "fasterrcnn_resnet50_fpn":
        raise ValueError("仅支持 Faster R-CNN ResNet50-FPN（非 V2）")
    # Explicitly disable both implicit weight downloads. Same BatchNorm layers in
    # the train/inference model; running statistics are preserved in state_dict.
    return fasterrcnn_resnet50_fpn(
        weights=None,
        weights_backbone=None,
        num_classes=int(config["num_classes"]),
        min_size=int(config.get("min_size", 600)),
        max_size=int(config.get("max_size", 1000)),
        box_score_thresh=float(config.get("box_score_thresh", 0.0)),
        box_nms_thresh=float(config.get("box_nms_thresh", 1.0)),
        box_detections_per_img=int(config.get("box_detections_per_img", 300)),
    )


def check_metadata(state, config):
    if not isinstance(state, dict) or "state_dict" not in state:
        return  # Existing bare torchvision/legacy state_dict remains compatible.
    if state.get("architecture", ARCHITECTURE) != ARCHITECTURE:
        raise ValueError("权重模型结构不是 Faster R-CNN ResNet50-FPN")
    if "class_names" in state:
        expected = model_config(state["class_names"])["_dataproc"]["category_map"]
        if config.get("_dataproc", {}).get("category_map") != expected:
            raise ValueError("权重类别顺序与推理配置不一致")
