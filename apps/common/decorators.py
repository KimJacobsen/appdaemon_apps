import functools
import time


def log_call(func):
    """Decorator to log entry and exit of a method."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        self.log(f"Calling '{func.__name__}'", level="INFO")
        result = func(self, *args, **kwargs)
        self.log(f"Done '{func.__name__}'", level="INFO")
        return result
    return wrapper


def requires_active_listener(func):
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        if not getattr(self, "active", True):
            self.log(f"Skipped '{func.__name__}' because listener is suspended.", level="INFO")
            return
        return func(self, *args, **kwargs)
    return wrapper


def handle_errors(level="ERROR"):
    """Decorator to catch and log exceptions in app methods."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                self.log(f"Exception in '{func.__name__}': {e}", level=level)
                return None
        return wrapper
    return decorator


def time_it(func):
    """Optional: log time taken by a method."""
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        start = time.time()
        result = func(self, *args, **kwargs)
        elapsed = time.time() - start
        self.log(f"'{func.__name__}' took {elapsed:.3f} seconds", level="INFO")
        return result
    return wrapper
