from . import nn


class Tensor:
    pass


def load(*args, **kwargs):
    raise RuntimeError("PyTorch wheel unavailable in isolated validation environment")
