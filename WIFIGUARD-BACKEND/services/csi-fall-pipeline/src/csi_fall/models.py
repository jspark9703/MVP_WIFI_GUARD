"""ACF encoders, optional CWT fusion, and an auxiliary ACF classifier."""
import torch
from torch import nn


class CompactMap(nn.Sequential):
    def __init__(self):
        layers = []
        for ci, co in [(1, 8), (8, 16), (16, 32)]:
            layers += [nn.Conv2d(ci, co, 3, stride=2, padding=1), nn.GELU()]
        super().__init__(*layers, nn.AdaptiveAvgPool2d((4, 4)), nn.Flatten(), nn.Linear(512, 64), nn.GELU())


class ImageResNet(nn.Module):
    def __init__(self, pretrained=False):
        super().__init__()
        from torchvision.models import resnet18, ResNet18_Weights
        self.net = resnet18(weights=ResNet18_Weights.DEFAULT if pretrained else None)
        self.net.fc = nn.Identity()

    def forward(self, x):
        x = nn.functional.interpolate(x, (224, 224), mode='bilinear', align_corners=False)
        return self.net(x.repeat(1, 3, 1, 1))


class FallModel(nn.Module):
    def __init__(self, kind='channel_mean', cwt=False, backbone='compact', pretrained=False):
        super().__init__()
        self.cwt = cwt
        if backbone not in ('compact', 'resnet18'):
            raise ValueError(backbone)
        if kind == 'scalars':
            self.encoder = nn.Sequential(nn.Linear(20, 64), nn.LayerNorm(64), nn.GELU(), nn.Dropout(.15), nn.Linear(64, 128), nn.LayerNorm(128), nn.GELU(), nn.Linear(128, 128))
            dim = 128
        elif kind == 'curve':
            self.encoder = nn.Sequential(nn.Conv1d(1, 32, 7, stride=2, padding=3), nn.GroupNorm(8, 32), nn.GELU(), nn.Conv1d(32, 64, 5, stride=2, padding=2), nn.GroupNorm(8, 64), nn.GELU(), nn.Conv1d(64, 128, 3, stride=2, padding=1), nn.GroupNorm(8, 128), nn.GELU(), nn.AdaptiveAvgPool1d(8), nn.Flatten(), nn.Linear(1024, 128), nn.LayerNorm(128))
            dim = 128
        else:
            self.encoder = CompactMap() if backbone == 'compact' else ImageResNet(pretrained)
            dim = 64 if backbone == 'compact' else 512
        if cwt:
            self.cwt_encoder = CompactMap() if backbone == 'compact' else ImageResNet(pretrained)
            cdim = 64 if backbone == 'compact' else 512
            self.auxiliary = nn.Linear(dim, 2)
            self.head = nn.Sequential(nn.LayerNorm(dim+cdim), nn.Linear(dim+cdim, 256), nn.GELU(), nn.Dropout(.3), nn.Linear(256, 2))
        else:
            # Matches the previous compact ACF-only topology (44,098 parameters).
            self.stat_encoder = nn.Sequential(nn.Linear(3, 16), nn.GELU())
            self.head = nn.Sequential(nn.Linear(dim+16, 64), nn.GELU(), nn.Dropout(.15), nn.Linear(64, 2))

    def forward(self, x, cwt=None):
        z = self.encoder(x)
        if self.cwt:
            zc = self.cwt_encoder(cwt)
            combined = torch.cat([zc, z], 1)
            return self.head(combined), combined, self.auxiliary(z)
        scalar = self.stat_encoder(torch.zeros(len(x), 3, device=x.device, dtype=x.dtype))
        return self.head(torch.cat([z, scalar], 1)), z, None
