import os
from typing import Callable, Mapping, Optional, Tuple

import torch

from ignite.distributed.comp_models.base import ComputationModel

try:
    import horovod.torch as hvd
    from horovod.run.runner import run as hvd_mp_spawn

    has_hvd_support = True
except ImportError:
    has_hvd_support = False


if has_hvd_support:

    HOROVOD = "horovod"

    class _HorovodDistModel(ComputationModel):
        """Private class for `Horovod <https://horovod.readthedocs.io/en/stable/>`_ distributed computation model.
        """

        name = "horovod-dist"

        available_backends = tuple([HOROVOD, ])

        @staticmethod
        def _get_hvd_rank():
            try:
                rank = hvd.rank()
            except ValueError as e:
                rank = -1
            return rank

        @staticmethod
        def create_from_context() -> Optional["_HorovodDistModel"]:
            rank = _HorovodDistModel._get_hvd_rank()
            if not (has_hvd_support and rank > -1):
                return None
            return _HorovodDistModel()

        @staticmethod
        def create_from_backend(backend: str, **kwargs) -> "_HorovodDistModel":
            if backend not in _HorovodDistModel.available_backends:
                raise ValueError("Backend should be one of '{}'".format(_HorovodDistModel.available_backends))

            rank = _HorovodDistModel._get_hvd_rank()
            if has_hvd_support and rank > -1:
                raise RuntimeError("Can not re-initialize Horovod if it is already initialized")
            return _HorovodDistModel(do_init=True, **kwargs)

        def __init__(self, do_init=False, **kwargs):
            """This is a private method. Please, use `create` or `create_from_context`
            """
            super(_HorovodDistModel, self).__init__()
            self._backend = HOROVOD
            if do_init:
                comm = kwargs.get("comm", None)
                hvd.init(comm=comm)

            self._local_rank = hvd.local_rank()

            if torch.cuda.is_available():
                torch.cuda.set_device(self._local_rank)

            self._setup_attrs()

        def _compute_nproc_per_node(self):
            return hvd.local_size()

        def get_local_rank(self) -> int:
            return self._local_rank

        def get_rank(self) -> int:
            return hvd.rank()

        def get_world_size(self) -> int:
            return hvd.size()

        def get_nproc_per_node(self) -> int:
            return self._nproc_per_node

        def get_nnodes(self) -> int:
            return self._nnodes

        def get_node_rank(self) -> int:
            return self._node

        def device(self) -> torch.device:
            if torch.cuda.is_available():
                index = torch.cuda.current_device()
                return torch.device("cuda:{}".format(index))
            return torch.device("cpu")

        def backend(self) -> str:
            return self._backend

        def finalize(self):
            hvd.shutdown()

        @staticmethod
        def _dist_worker_task_fn(
            backend, fn, args, kwargs_dict
        ):
            from ignite.distributed.utils import _set_model, finalize

            model = _HorovodDistModel.create_from_backend(backend)
            _set_model(model)
            fn(model.get_local_rank(), *args, **kwargs_dict)
            finalize()

        @staticmethod
        def spawn(
            fn: Callable,
            args: Tuple,
            kwargs_dict: Optional[Mapping] = None,
            nproc_per_node: int = 1,
            backend: str = HOROVOD,
            **kwargs
        ):
            hvd_mp_spawn(
                _HorovodDistModel._dist_worker_task_fn,
                args=(HOROVOD, fn, args, kwargs_dict),
                np=nproc_per_node,
                **kwargs
            )

        # _reduce_op_map = {
        #     "SUM": dist.ReduceOp.SUM,
        #     "PRODUCT": dist.ReduceOp.PRODUCT,
        #     "MIN": dist.ReduceOp.MIN,
        #     "MAX": dist.ReduceOp.MAX,
        #     "AND": dist.ReduceOp.BAND,
        #     "OR": dist.ReduceOp.BOR,
        # }

        def _do_all_reduce(self, tensor: torch.Tensor, op: str = "SUM") -> torch.Tensor:
            raise NotImplementedError("TODO")
            # if op not in self._reduce_op_map:
            #     raise ValueError("Unsupported reduction operation: '{}'".format(op))
            # op = self._reduce_op_map[op]
            # dist.all_reduce(tensor, op)
            # return tensor

        def _do_all_gather(self, tensor: torch.Tensor) -> torch.Tensor:
            raise NotImplementedError("TODO")
            # if tensor.ndimension() == 0:
            #     tensor = tensor.unsqueeze(0)
            # output = [torch.zeros_like(tensor) for _ in range(self.get_world_size())]
            # dist.all_gather(output, tensor)
            # return torch.cat(output, dim=0)

        def barrier(self):
            # https://github.com/horovod/horovod/issues/159#issuecomment-424834603
            # hvd.allreduce(torch.tensor(0, device=self.device()), name="barrier")
            hvd.allreduce(torch.tensor(0, device="cpu"), name="barrier")