"""PCA-free multichannel ACF and time-lag products. All filters stay inside each window."""
import numpy as np
import torch
from torch.nn import functional as F
from scipy.signal import savgol_coeffs

KINDS=['mean_signal','channel_mean','channel_energy','channel_savgol','channel_hampel','channel_detrend','channel_difference','channel_acf']

def center(x):return x-x.double().mean(dim=1,keepdim=True).to(x.dtype)

def filter_window(x,fs,method):
    # x: batch,time,channel. Near51ms fixed before evaluation.
    width=max(5,int(round(.051*fs))|1);radius=width//2
    if method=='channel_savgol':
        coeff=torch.tensor(savgol_coeffs(width,2),device=x.device,dtype=x.dtype)[None,None]
        y=x.transpose(1,2);padded=F.pad(y,(radius,radius),mode='reflect')
        return F.conv1d(padded,coeff.expand(x.shape[2],1,width),groups=x.shape[2]).transpose(1,2)
    if method=='channel_hampel':
        padded=F.pad(x.transpose(1,2),(radius,radius),mode='reflect').unfold(-1,width,1)
        median=padded.median(dim=-1).values
        mad=(padded-median.unsqueeze(-1)).abs().median(dim=-1).values
        y=x.transpose(1,2);mask=(y-median).abs()>3*1.4826*mad.clamp_min(1e-8)
        return torch.where(mask,median,y).transpose(1,2)
    if method=='channel_detrend':
        c=center(x);t=torch.linspace(-1,1,x.shape[1],device=x.device,dtype=x.dtype)[None,:,None]
        slope=(c*t).sum(dim=1,keepdim=True)/t.square().sum()
        return c-slope*t
    if method=='channel_difference':return torch.cat([torch.zeros_like(x[:,:1]),x[:,1:]-x[:,:-1]],dim=1)
    return x

def interpolate_lags(values,fs):
    # Native rows include ceil(.4*fs), enclosing the common final lag.
    coord=torch.linspace(0,.4,129,device=values.device,dtype=values.dtype)*fs
    lo=coord.floor().long().clamp_max(values.shape[1]-1);hi=(lo+1).clamp_max(values.shape[1]-1)
    w=(coord-lo).view(1,129,*([1]*(values.ndim-2)))
    return values[:,lo]*(1-w)+values[:,hi]*w

def channel_products(x,fs,extras=True):
    c=center(x);energy=c.square().sum(dim=1);active=energy>1e-12
    u=c/energy.clamp_min(1e-12).sqrt()[:,None,:]
    u=u*active[:,None,:];count=active.sum(-1).clamp_min(1)
    weight=energy/energy.sum(-1,keepdim=True).clamp_min(1e-12)
    n=x.shape[1];last=int(np.ceil(.4*fs));edges=torch.ceil(torch.arange(65,device=x.device,dtype=torch.float64)*n/64).long()
    means=[];weighted=[];curves=[]
    for lag in range(last+1):
        prod=u[:,lag:]*u[:,:n-lag]
        a=prod.sum(-1)/count[:,None]
        a=F.pad(a,(lag+1,0)).cumsum(-1)
        means.append((a[:,edges[1:]]-a[:,edges[:-1]])*64)
        if extras:
            curves.append(prod.sum(dim=1))
            b=(prod*weight[:,None,:]).sum(-1)
            b=F.pad(b,(lag+1,0)).cumsum(-1)
            weighted.append((b[:,edges[1:]]-b[:,edges[:-1]])*64)
    mean=interpolate_lags(torch.stack(means,1),fs)
    if not extras:return mean[:,None],None,None,active
    weighted=interpolate_lags(torch.stack(weighted,1),fs)
    curve=interpolate_lags(torch.stack(curves,1),fs)
    return mean[:,None],weighted[:,None],curve[:,None],active

@torch.no_grad()
def extract_all(x,fs):
    assert x.ndim==3 and x.shape[-1]==30
    x=x.float();base=center(x);baseenergy=base.square().sum((1,2)).clamp_min(1e-12)
    mean,energy,curves,active=channel_products(x,fs)
    out=dict(channel_mean=mean,channel_energy=energy,channel_acf=curves)
    audit={'inactive_channels':(~active).sum(1).cpu().numpy(),
        'channel_mean_recovery_error':(mean[:,0].mean(-1)-(curves[:,0]*active[:,None,:]).sum(-1)/active.sum(-1).clamp_min(1)[:,None]).abs().amax(-1).cpu().numpy()}
    for method in ['mean_signal','channel_savgol','channel_hampel','channel_detrend','channel_difference']:
        y=x.mean(-1,keepdim=True) if method=='mean_signal' else filter_window(x,fs,method)
        out[method]=channel_products(y,fs,extras=False)[0]
        if method!='mean_signal':
            c=center(y);audit[method+'_energy_ratio']=(c.square().sum((1,2))/baseenergy).cpu().numpy()
            audit[method+'_peak_ratio']=(c.abs().amax((1,2))/base.abs().amax((1,2)).clamp_min(1e-12)).cpu().numpy()
            if method=='channel_hampel':audit['hampel_changed_fraction']=((y-x).abs()>0).float().mean((1,2)).cpu().numpy()
    return {k:v.cpu().numpy().astype(np.float16) for k,v in out.items()},audit
