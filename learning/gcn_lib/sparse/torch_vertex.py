import torch
from torch import nn
import torch.nn.functional as F
import torch_geometric as tg
from torch_geometric.nn import GatedGraphConv
from .torch_nn import MLP, act_layer, norm_layer, MultiSeq
from .torch_edge import DilatedKnnGraph
from torch_geometric.utils import remove_self_loops, add_self_loops


class MRConv(nn.Module):
    """
    Max-Relative Graph Convolution (Paper: https://arxiv.org/abs/1904.03751)
    """
    def __init__(self, in_channels, out_channels, act='relu', norm=None, bias=True, aggr='max'):
        super(MRConv, self).__init__()
        self.nn = MLP([in_channels*2, out_channels], act, norm, bias)
        self.aggr = aggr

    def forward(self, x, edge_index):
        """"""
        x_j = tg.utils.scatter_(self.aggr, torch.index_select(x, 0, edge_index[0]) - torch.index_select(x, 0, edge_index[1]), edge_index[1], dim_size=x.shape[0])
        return self.nn(torch.cat([x, x_j], dim=1))


class EdgConv(tg.nn.EdgeConv):
    """
    Edge convolution layer (with activation, batch normalization)
    """
    def __init__(self, in_channels, out_channels, act='relu', norm=None, bias=True, aggr='max'):
        #super(EdgConv, self).__init__(MLP([in_channels*2, out_channels], act, norm, bias), aggr)
        super(EdgConv, self).__init__(MultiSeq(*[MLP([in_channels*2, out_channels], act, norm, bias),  #64 128
									 MLP([out_channels, out_channels], act, norm, bias),										#128 64
									 torch.nn.Dropout(p=0.1),
									 MLP([out_channels, out_channels], None, None, bias)]))
    def forward(self, x, edge_index):
        return super(EdgConv, self).forward(x, edge_index)


class GATConv(nn.Module):
    """
    Edge convolution layer (with activation, batch normalization)
    """
    def __init__(self, in_channels, out_channels,  act='relu', norm=None, bias=True, heads=8):
        super(GATConv, self).__init__()
        self.gconv = tg.nn.GATConv(in_channels, out_channels, heads, bias=bias)
        m =[]
        if act:
            m.append(act_layer(act))
        if norm:
            m.append(norm_layer(norm, in_channels))
        self.unlinear = nn.Sequential(*m)

    def forward(self, x, edge_index):
        out = self.unlinear(self.gconv(x, edge_index))
        return out


class SAGEConv(tg.nn.SAGEConv):
    r"""The GraphSAGE operator from the `"Inductive Representation Learning on
    Large Graphs" <https://arxiv.org/abs/1706.02216>`_ paper

    .. math::
        \mathbf{\hat{x}}_i &= \mathbf{\Theta} \cdot
        \mathrm{mean}_{j \in \mathcal{N(i) \cup \{ i \}}}(\mathbf{x}_j)

        \mathbf{x}^{\prime}_i &= \frac{\mathbf{\hat{x}}_i}
        {\| \mathbf{\hat{x}}_i \|_2}.

    Args:
        in_channels (int): Size of each input sample.
        out_channels (int): Size of each output sample.
        normalize (bool, optional): If set to :obj:`False`, output features
            will not be :math:`\ell_2`-normalized. (default: :obj:`True`)
        bias (bool, optional): If set to :obj:`False`, the layer will not learn
            an additive bias. (default: :obj:`True`)
        **kwargs (optional): Additional arguments of
            :class:`torch_geometric.nn.conv.MessagePassing`.
    """

    def __init__(self,
                 in_channels,
                 out_channels,
                 nn,
                 norm=True,
                 bias=True,
                 relative=False,
                 **kwargs):
        self.relative = relative
        if norm is not None:
            super(SAGEConv, self).__init__(in_channels, out_channels, True, bias, **kwargs)
        else:
            super(SAGEConv, self).__init__(in_channels, out_channels, False, bias, **kwargs)
        self.nn = nn

    def forward(self, x, edge_index, size=None):
        """"""
        if size is None:
            edge_index, _ = remove_self_loops(edge_index)
            edge_index, _ = add_self_loops(edge_index, num_nodes=x.size(0))

        x = x.unsqueeze(-1) if x.dim() == 1 else x
        return self.propagate(edge_index, size=size, x=x)

    def message(self, x_i, x_j):
        if self.relative:
            x = torch.matmul(x_j - x_i, self.weight)
        else:
            x = torch.matmul(x_j, self.weight)
        return x

    def update(self, aggr_out, x):
        out = self.nn(torch.cat((x, aggr_out), dim=1))
        if self.bias is not None:
            out = out + self.bias
        if self.normalize:
            out = F.normalize(out, p=2, dim=-1)
        return out


class RSAGEConv(SAGEConv):
    """
    Edge convolution layer (with activation, batch normalization)
    """

    def __init__(self, in_channels, out_channels, act='relu', norm=None, bias=True, relative=False):
        nn = MLP([out_channels + in_channels, out_channels], act, norm, bias)
        super(RSAGEConv, self).__init__(in_channels, out_channels, nn, norm, bias, relative)


class SemiGCNConv(nn.Module):
    """
    Edge convolution layer (with activation, batch normalization)
    """

    def __init__(self, in_channels, out_channels, act='relu', norm=None, bias=True):
        super(SemiGCNConv, self).__init__()
        self.gconv = tg.nn.GCNConv(in_channels, out_channels, bias=bias)
        m = []
        if act:
            m.append(act_layer(act))
        if norm:
            m.append(norm_layer(norm, out_channels))
        self.unlinear = nn.Sequential(*m)

    def forward(self, x, edge_index):
        out = self.unlinear(self.gconv(x, edge_index))
        return out


class GinConv(tg.nn.GINConv):
    """
    Edge convolution layer (with activation, batch normalization)
    """
    def __init__(self, in_channels, out_channels, act='relu', norm=None, bias=True, aggr='add'):
        super(GinConv, self).__init__(MLP([in_channels, out_channels], act, norm, bias))

    def forward(self, x, edge_index):
        return super(GinConv, self).forward(x, edge_index)


class GraphConv(nn.Module):
    """
    Static graph convolution layer
    """
    def __init__(self, in_channels, out_channels, conv='edge',
                 act='relu', norm=None, bias=True, heads=8):
        super(GraphConv, self).__init__()
        if conv.lower() == 'edge':
            self.gconv = EdgConv(in_channels, out_channels, act, norm, bias)
        elif conv.lower() == 'mr':
            self.gconv = MRConv(in_channels, out_channels, act, norm, bias)
        elif conv.lower() == 'gat':
            self.gconv = GATConv(in_channels, out_channels//heads, act, norm, bias, heads)
        elif conv.lower() == 'gcn':
            self.gconv = SemiGCNConv(in_channels, out_channels, act, norm, bias)
        elif conv.lower() == 'gin':
            self.gconv = GinConv(in_channels, out_channels, act, norm, bias)
        elif conv.lower() == 'sage':
            self.gconv = RSAGEConv(in_channels, out_channels, act, norm, bias, False)
        elif conv.lower() == 'rsage':
            self.gconv = RSAGEConv(in_channels, out_channels, act, norm, bias, True)
        elif conv.lower() == 'gru':
            self.gconv = GatedGraphConv(out_channels, 5, aggr='add', bias=True)
        else:
            raise NotImplementedError('conv {} is not implemented'.format(conv))

    def forward(self, x, edge_index):
        return self.gconv(x, edge_index)


class DynConv(GraphConv):
    """
    Dynamic graph convolution layer
    """
    def __init__(self, in_channels, out_channels, kernel_size=9, dilation=1, conv='edge', act='relu',
                 norm=None, bias=True, heads=8, **kwargs):
        super(DynConv, self).__init__(in_channels, out_channels, conv, act, norm, bias, heads)
        self.k = kernel_size
        self.d = dilation
        self.dilated_knn_graph = DilatedKnnGraph(kernel_size, dilation, **kwargs)

    def forward(self, x, batch=None):
        edge_index = self.dilated_knn_graph(x, batch)
        return super(DynConv, self).forward(x, edge_index)


class ResDynBlock(nn.Module):
    """
    Residual Dynamic graph convolution block
    """
    def __init__(self, channels,  kernel_size=9, dilation=1, conv='edge', act='relu', norm=None,
                 bias=True, res_scale=1, **kwargs):
        super(ResDynBlock, self).__init__()
        self.body = DynConv(channels, channels, kernel_size, dilation, conv,
                            act, norm, bias, **kwargs)
        self.res_scale = res_scale

    def forward(self, x, batch=None):
        #print("res block output", (self.body(x, batch) + x*self.res_scale).shape)
        return self.body(x, batch) + x*self.res_scale, batch


class DenseDynBlock(nn.Module):
    """
    Dense Dynamic graph convolution block
    """
    def __init__(self, channels,  kernel_size=9, dilation=1, conv='edge', act='relu', norm=None, bias=True, **kwargs):
        super(DenseDynBlock, self).__init__()
        self.body = DynConv(channels*2, channels, kernel_size, dilation, conv,
                            act, norm, bias, **kwargs)

    def forward(self, x, batch=None):
        dense = self.body(x, batch)
        return torch.cat((x, dense), 1), batch


class ResGraphBlock(nn.Module):
    """
    Residual Static graph convolution block
    """
    def __init__(self, channels,  conv='edge', act='relu', norm=None, bias=True, heads=8,  res_scale=1):
        super(ResGraphBlock, self).__init__()
        self.body = GraphConv(channels, channels, conv, act, norm, bias, heads)
        self.res_scale = res_scale

    def forward(self, x, edge_index):
        return self.body(x, edge_index) + x*self.res_scale, edge_index


class DenseGraphBlock(nn.Module):
    """
    Residual Static graph convolution block
    """
    def __init__(self, in_channels,  out_channels, conv='edge', act='relu', norm=None, bias=True, heads=8):
        super(DenseGraphBlock, self).__init__()
        self.body = GraphConv(in_channels, out_channels, conv, act, norm, bias, heads)

    def forward(self, x, edge_index):
        dense = self.body(x, edge_index)
        return torch.cat((x, dense), 1), edge_index

