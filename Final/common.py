"""
This module contains classes and functions that are common across both, one-stage
and two-stage detector implementations. You have to implement some parts here -
walk through the notebooks and you will find instructions on *when* to implement
*what* in this module.
"""
from typing import Dict, Tuple

import torch
from torch import dtype, nn
from torch.nn import functional as F
from torchvision import models
from torchvision.models import feature_extraction


def hello_common():
    print("Hello from common.py!")


class DetectorBackboneWithFPN(nn.Module):
    r"""
    Detection backbone network: A tiny RegNet model coupled with a Feature
    Pyramid Network (FPN). This model takes in batches of input images with
    shape `(B, 3, H, W)` and gives features from three different FPN levels
    with shapes and total strides upto that level:

        - level p3: (3, H /  8, W /  8)      stride =  8
        - level p4: (3, H / 16, W / 16)      stride = 16
        - level p5: (3, H / 32, W / 32)      stride = 32

    NOTE: We could use any convolutional network architecture that progressively
    downsamples the input image and couple it with FPN. We use a small enough
    backbone that can work with Colab GPU and get decent enough performance.
    """

    def __init__(self, out_channels: int):
        super().__init__()
        self.out_channels = out_channels

        # Initialize with ImageNet pre-trained weights.
        _cnn = models.regnet_y_32gf(pretrained="IMAGENET1K_V1")
        node_names = feature_extraction.get_graph_node_names(_cnn)
        print(node_names)
        # Torchvision models only return features from the last level. Detector
        # backbones (with FPN) require intermediate features of different scales.
        # So we wrap the ConvNet with torchvision's feature extractor. Here we
        # will get output features with names (c3, c4, c5) with same stride as
        # (p3, p4, p5) described above.
        self.backbone = feature_extraction.create_feature_extractor(
            _cnn,
            return_nodes={
               "trunk_output.block2": "c3",
               "trunk_output.block3": "c4",
               "trunk_output.block4": "c5",
            },
        )

        # Pass a dummy batch of input images to infer shapes of (c3, c4, c5).
        # Features are a dictionary with keys as defined above. Values are
        # batches of tensors in NHWC format, that give intermediate features
        # from the backbone network.
        dummy_out = self.backbone(torch.randn(2, 3, 224, 224))
        dummy_out_shapes = [(key, value.shape) for key, value in dummy_out.items()]

        print("For dummy input images with shape: (2, 3, 224, 224)")
        for level_name, feature_shape in dummy_out_shapes:
            print(f"Shape of {level_name} features: {feature_shape}")

        ######################################################################
        # TODO: Initialize additional Conv layers for FPN.                   #
        # HINT: You have to use `dummy_out_shapes` defined above to decide   #
        # the input/output channels of these layers.                         #
        ######################################################################
        # This behaves like a Python dict, but makes PyTorch understand that
        # there are trainable weights inside it.
        self.fpn_params = nn.ModuleDict()

        # Replace "pass" statement with your code
        self.fpn_params['conv5'] = nn.Conv2d(dummy_out['c5'].shape[1], out_channels, 1)
        self.fpn_params['conv4'] = nn.Conv2d(dummy_out['c4'].shape[1], out_channels, 1)
        self.fpn_params['conv3'] = nn.Conv2d(dummy_out['c3'].shape[1], out_channels, 1)

        self.fpn_params['conv_out5'] = nn.Conv2d(out_channels, out_channels, 3, stride=1, padding=1)
        self.fpn_params['conv_out4'] = nn.Conv2d(out_channels, out_channels, 3, stride=1, padding=1)
        self.fpn_params['conv_out3'] = nn.Conv2d(out_channels, out_channels, 3, stride=1, padding=1)
        ################################################################
        #                      END OF YOUR CODE                        #
        ################################################################

    @property
    def fpn_strides(self):
        """
        Total stride up to the FPN level. For a fixed ConvNet, these values
        are invariant to input image size. You may access these values freely
        to implement your logic in FCOS / Faster R-CNN.
        """
        return {"p3": 8, "p4": 16, "p5": 32}

    def forward(self, images: torch.Tensor):

        # Multi-scale features, dictionary with keys: {"c3", "c4", "c5"}.
        backbone_feats = self.backbone(images)

        fpn_feats = {"p3": None, "p4": None, "p5": None}
        ######################################################################
        # TODO: Fill output FPN features (p3, p4, p5) using RegNet features  #
        # (c3, c4, c5) and FPN conv layers created above.                    #
        # HINT: Use `F.interpolate` to upsample FPN features.                #
        ######################################################################

        # Replace "pass" statement with your code
        p5_conv1_1=self.fpn_params['conv5'](backbone_feats["c5"])
        p5_upsampled_x=F.interpolate(p5_conv1_1,size=(backbone_feats["c4"].shape[2], backbone_feats["c4"].shape[3]),mode="nearest")
        p5_x=self.fpn_params['conv_out5'](p5_conv1_1)

        p4_conv1_1=self.fpn_params['conv4'](backbone_feats["c4"])
        p4_x=p5_upsampled_x+p4_conv1_1
        p4_upsampled_x=F.interpolate(p4_x,size=(backbone_feats["c3"].shape[2], backbone_feats["c3"].shape[3]),mode="nearest")
        p4_x = self.fpn_params["conv_out4"](p4_x)
        
        p3_conv1_1 = self.fpn_params["conv3"](backbone_feats["c3"])
        p3_x = p3_conv1_1 + p4_upsampled_x
        p3_x = self.fpn_params["conv_out3"](p3_x)

        fpn_feats["p5"] = p5_x
        fpn_feats["p4"] = p4_x
        fpn_feats["p3"] = p3_x

        ################################################################
        #                      END OF YOUR CODE                        #
        ################################################################

        return fpn_feats


def get_fpn_location_coords(
    shape_per_fpn_level: Dict[str, Tuple],
    strides_per_fpn_level: Dict[str, int],
    dtype: torch.dtype = torch.float32,
    device: str = "cpu",
) -> Dict[str, torch.Tensor]:
    """
    Map every location in FPN feature map to a point on the image. This point
    represents the center of the receptive field of this location. We need to
    do this for having a uniform co-ordinate representation of all the locations
    across FPN levels, and GT boxes.

    Args:
        shape_per_fpn_level: Shape of the FPN feature level, dictionary of keys
            {"p3", "p4", "p5"} and feature shapes `(B, C, H, W)` as values.
        strides_per_fpn_level: Dictionary of same keys as above, each with an
            integer value giving the stride of corresponding FPN level.
            See `backbone.py` for more details.

    Returns:
        Dict[str, torch.Tensor]
            Dictionary with same keys as `shape_per_fpn_level` and values as
            tensors of shape `(H * W, 2)` giving `(xc, yc)` co-ordinates of the
            centers of receptive fields of the FPN locations, on input image.
    """
    # Set these to `(N, 2)` Tensors giving absolute location co-ordinates.
    location_coords = {
        level_name: None for level_name, _ in shape_per_fpn_level.items()
    }

    for level_name, feat_shape in shape_per_fpn_level.items():
        level_stride = strides_per_fpn_level[level_name]

        ######################################################################
        # TODO: Implement logic to get location co-ordinates below.          #
        ######################################################################
        # Replace "pass" statement with your code
        cur = []
        H = shape_per_fpn_level[level_name][2]
        W = shape_per_fpn_level[level_name][3]
        for i in range(H):
          for j in range(W):
            cur.append([level_stride*(j+0.5),level_stride*(i+0.5)])
        location_coords[level_name]=torch.tensor(cur,device=device)
        ######################################################################
        #                             END OF YOUR CODE                       #
        ######################################################################
    return location_coords

def nms(boxes: torch.Tensor, scores: torch.Tensor, iou_threshold: float = 0.5):
    """
    Non-maximum suppression removes overlapping bounding boxes.

    Args:
        boxes: Tensor of shape (N, 4) giving top-left and bottom-right coordinates
            of the bounding boxes to perform NMS on.
        scores: Tensor of shpe (N, ) giving scores for each of the boxes.
        iou_threshold: Discard all overlapping boxes with IoU > iou_threshold

    Returns:
        keep: torch.long tensor with the indices of the elements that have been
            kept by NMS, sorted in decreasing order of scores;
            of shape [num_kept_boxes]
    """

    if (not boxes.numel()) or (not scores.numel()):
        return torch.zeros(0, dtype=torch.long)

    keep = None
    #############################################################################
    # TODO: Implement non-maximum suppression which iterates the following:     #
    #       1. Select the highest-scoring box among the remaining ones,         #
    #          which has not been chosen in this step before                    #
    #       2. Eliminate boxes with IoU > threshold                             #
    #       3. If any boxes remain, GOTO 1                                      #
    #       Your implementation should not depend on a specific device type;    #
    #       you can use the device of the input if necessary.                   #
    # HINT: You can refer to the torchvision library code:                      #
    # github.com/pytorch/vision/blob/main/torchvision/csrc/ops/cpu/nms_kernel.cpp
    #############################################################################
    # Replace "pass" statement with your code
    x1 = boxes[:,0]
    y1 = boxes[:,1]
    x2 = boxes[:,2]
    y2 = boxes[:,3]
    
    areas = (x2 - x1) * (y2 - y1)
    order = scores.argsort()
    
    c = 0
    result = []
    while len(order) > 0:
        # find the biggest score
        idx = order[-1]
        result.append(idx)
        order = order[:-1]
        if len(order) == 0:
            break
        # select coordinates of BBoxes according to the indices in order
        xx1 = torch.index_select(x1,dim = 0, index = order)
        xx2 = torch.index_select(x2,dim = 0, index = order)
        yy1 = torch.index_select(y1,dim = 0, index = order)
        yy2 = torch.index_select(y2,dim = 0, index = order)
    
        # find the coordinates of the intersection boxes
        xx1 = torch.max(xx1, x1[idx])
        yy1 = torch.max(yy1, y1[idx])
        xx2 = torch.min(xx2, x2[idx])
        yy2 = torch.min(yy2, y2[idx])
    
        # find height and width of the intersection boxes
        w = xx2 - xx1
        h = yy2 - yy1
    
        # take max with 0.0 to avoid negative w and h due to non-overlapping boxes
        w = torch.clamp(w, min=0.0)
        h = torch.clamp(h, min=0.0)
    
        # intersection
        inter = w * h
    
        # areas of BBoxes according the indices in order
        rem_area = torch.index_select(areas, dim = 0, index = order)
        
        # find union of every prediction T in boxes
        union = (rem_area - inter) + areas[idx]
        
        # calculate IoU
        IoU = inter/union
        
        # keep the boxes with IoU less than thresh_iou
        mask = IoU < iou_threshold
        order = order[mask]
    
    keep = torch.stack(result)
    #############################################################################
    #                              END OF YOUR CODE                             #
    #############################################################################
    return keep


def class_spec_nms(
    boxes: torch.Tensor,
    scores: torch.Tensor,
    class_ids: torch.Tensor,
    iou_threshold: float = 0.5,
):
    """
    Wrap `nms` to make it class-specific. Pass class IDs as `class_ids`.
    STUDENT: This depends on your `nms` implementation.

    Returns:
        keep: torch.long tensor with the indices of the elements that have been
            kept by NMS, sorted in decreasing order of scores;
            of shape [num_kept_boxes]
    """
    if boxes.numel() == 0:
        return torch.empty((0,), dtype=torch.int64, device=boxes.device)
    max_coordinate = boxes.max()
    offsets = class_ids.to(boxes) * (max_coordinate + torch.tensor(1).to(boxes))
    boxes_for_nms = boxes + offsets[:, None] 
    keep = nms(boxes_for_nms, scores, iou_threshold)
    return keep
