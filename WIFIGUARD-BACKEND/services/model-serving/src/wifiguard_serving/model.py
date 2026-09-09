"""DualBranchResNet 모델 정의.

Window3BestModelInference/scripts/train_losnlos_resnet18_s3_acf_dual_end2end.py 와
train_losnlos_resnet18_s3_acf_fusion.py 에서 추론에 필요한 클래스만 이식했다.
체크포인트(state_dict) 키와 1:1 대응해야 하므로 구조 변경 금지.
"""

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as F

RESNET_BLOCKS = {
    "resnet18": (2, 2, 2, 2),
    "resnet34": (3, 4, 6, 3),
}


def prepare_resnet_image(x: torch.Tensor, image_size: int) -> torch.Tensor:
    if x.ndim != 4:
        raise ValueError(f"Expected BCHW tensor, got {tuple(x.shape)}")
    if x.shape[1] == 1:
        x = x.repeat(1, 3, 1, 1)
    elif x.shape[1] == 2:
        x = torch.cat([x, x.mean(dim=1, keepdim=True)], dim=1)
    elif x.shape[1] > 3:
        x = x[:, :3]
    if x.shape[-2:] != (image_size, image_size):
        x = F.interpolate(x, size=(image_size, image_size), mode="bilinear", align_corners=False)
    return x


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes: int, planes: int, stride: int = 1, downsample: nn.Module | None = None) -> None:
        super().__init__()
        self.conv1 = nn.Conv2d(inplanes, planes, kernel_size=3, stride=stride, padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(planes, planes, kernel_size=3, stride=1, padding=1, bias=False)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        identity = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        if self.downsample is not None:
            identity = self.downsample(x)
        return self.relu(out + identity)


class ResNetImageEncoder(nn.Module):
    def __init__(self, backbone: str, embedding_dim: int = 512, dropout: float = 0.2) -> None:
        super().__init__()
        if backbone not in RESNET_BLOCKS:
            raise ValueError(f"unsupported backbone={backbone!r}")
        self.backbone = backbone
        self.inplanes = 64
        blocks = RESNET_BLOCKS[backbone]
        self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
        self.bn1 = nn.BatchNorm2d(64)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(64, blocks[0])
        self.layer2 = self._make_layer(128, blocks[1], stride=2)
        self.layer3 = self._make_layer(256, blocks[2], stride=2)
        self.layer4 = self._make_layer(512, blocks[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.projection = nn.Sequential(
            nn.Dropout(dropout),
            nn.Linear(512, embedding_dim),
            nn.LayerNorm(embedding_dim),
            nn.ReLU(inplace=True),
        )

    def _make_layer(self, planes: int, blocks: int, stride: int = 1) -> nn.Sequential:
        downsample = None
        if stride != 1 or self.inplanes != planes:
            downsample = nn.Sequential(
                nn.Conv2d(self.inplanes, planes, kernel_size=1, stride=stride, bias=False),
                nn.BatchNorm2d(planes),
            )
        layers: list[nn.Module] = [BasicBlock(self.inplanes, planes, stride, downsample)]
        self.inplanes = planes
        for _ in range(1, blocks):
            layers.append(BasicBlock(self.inplanes, planes))
        return nn.Sequential(*layers)

    def forward_features(self, x: torch.Tensor) -> torch.Tensor:
        x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)
        return torch.flatten(self.avgpool(x), 1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.projection(self.forward_features(x))


class DualBranchResNet(nn.Module):
    def __init__(self, backbone: str, embedding_dim: int, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.encoder_a = ResNetImageEncoder(backbone=backbone, embedding_dim=embedding_dim, dropout=dropout)
        self.encoder_b = ResNetImageEncoder(backbone=backbone, embedding_dim=embedding_dim, dropout=dropout)
        concat_dim = int(embedding_dim) * 2
        self.classifier = nn.Sequential(
            nn.LayerNorm(concat_dim),
            nn.Linear(concat_dim, hidden_dim),
            nn.ReLU(inplace=True),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 2),
        )

    def forward(self, x_a: torch.Tensor, x_b: torch.Tensor, image_size: int) -> dict[str, torch.Tensor]:
        z_a = self.encoder_a(prepare_resnet_image(x_a, image_size))
        z_b = self.encoder_b(prepare_resnet_image(x_b, image_size))
        embedding = torch.cat([z_a, z_b], dim=1)
        return {
            "embedding_a": z_a,
            "embedding_b": z_b,
            "embedding": embedding,
            "logits": self.classifier(embedding),
        }
