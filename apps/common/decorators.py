import functools
import time
from typing import Callable, Type, TypeVar
import debugpy

T = TypeVar('T')

def debugpy_init(port: int = 5678):
    """Decorator to initialize debugpy with a specified port.
    
    Args:
        port: Port number for debugpy to listen on. Defaults to 5678.
    """
    def decorator(cls: Type[T]) -> Type[T]:
        original_init = cls.initialize
        
        @functools.wraps(original_init)
        def wrapped_init(self, *args, **kwargs):
            # Only initialize debugpy for the first instance
            if not hasattr(cls, '_debugpy_initialized'):
                try:
                    debugpy.listen(("localhost", port))
                    debugpy.wait_for_client()
                    self.log(f"Debugpy: Waiting for client to connect on port {port}")
                    setattr(cls, '_debugpy_initialized', True)
                except RuntimeError:
                    self.log("Debugpy is already initialized.")
                    setattr(cls, '_debugpy_initialized', True)
            
            # Call the original initialize method
            return original_init(self, *args, **kwargs)
            
        cls.initialize = wrapped_init
        return cls
    
    return decorator


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


def handle_errors(*decorator_args, **decorator_kwargs):
    """Decorator to catch and log exceptions in app methods.
    
    Args:
        *decorator_args: Variable positional arguments
        **decorator_kwargs: Variable keyword arguments
            level: Log level for errors (default: "ERROR")
            return_value: Value to return on error (default: None)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                level = decorator_kwargs.get('level', "ERROR")
                return_value = decorator_kwargs.get('return_value', None)
                self.log(f"Exception in '{func.__name__}': {e}", level=level)
                return return_value
        return wrapper
    
    # Handle case where decorator is used without arguments
    if len(decorator_args) == 1 and callable(decorator_args[0]):
        return decorator(decorator_args[0])
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
