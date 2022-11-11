import torch
import torch.nn as nn
import torch.nn.functional as F
from utils.utils import *
import warnings
warnings.filterwarnings("ignore")


class TerminationNetwork(torch.nn.Module):
    def __init__(self, action_size, hidden_size=100):
        super(TerminationNetwork, self).__init__()
        self.hidden = nn.Linear(hidden_size + action_size, hidden_size)
        self.output = torch.nn.Linear(hidden_size, 1)
        self.tanh = nn.Tanh()
        self.sigmoid = nn.Sigmoid()

    def forward(self, x, y):
        x = x.repeat(1, y.size(1), 1)
        x = torch.cat((x, y), dim=2)
        x = self.hidden(x)
        x = self.tanh(x)
        x = self.output(x)
        x = self.sigmoid(x)
        return torch.mean(x.squeeze())


if __name__ == "__main__":
    t = TerminationNetwork(2,1)
    print(t(torch.Tensor([[1,2]])))