class Module:
    pass


class _Layer:
    def __init__(self, *args, **kwargs):
        pass


Sequential = Conv1d = BatchNorm1d = ReLU = AdaptiveAvgPool1d = Flatten = Dropout = Linear = _Layer
