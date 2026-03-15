__version__ = "0.8.2"

import os

os.environ["GRPC_VERBOSITY"] = "NONE"
os.environ["GRPC_TRACE"] = ""
os.environ["GLOG_minloglevel"] = "2"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

import sys  # noqa: E402
import warnings  # noqa: E402

if not sys.flags.dev_mode:
    warnings.filterwarnings("ignore", category=DeprecationWarning, module="google")
    warnings.filterwarnings("ignore", category=FutureWarning, module="google")

    try:
        import absl.logging  # type: ignore[import-not-found]

        absl.logging.set_verbosity("error")
        absl.logging.set_stderrthreshold("error")
    except ImportError:
        pass
