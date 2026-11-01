"""Explicit per-test application composition; never patches module resources."""
import os
import tempfile
import time
from contextlib import contextmanager, ExitStack
from unittest.mock import patch
from backend import main
from backend.core.world import World


def app_for(*, world=None, cameras=None, vision=None):
    root=tempfile.mkdtemp(prefix='app-fixture-',dir=os.environ['HUYAN_OFFLINE_ROOT'])
    complete=world if isinstance(world,World) else None
    dependencies=main.build_dependencies(data_dir=complete._data_dir if complete is not None else root,
                                         world=complete,cameras=cameras)
    if world is not None:
        if not isinstance(world,World):
            # A partial fake supplies only behavior under test; the complete
            # offline World provides inert stores needed by router assembly.
            dependencies.world.__dict__.update(vars(world))
    if vision is not None:dependencies.vision=vision
    return main.create_app(dependencies)


@contextmanager
def application_for(*, world=None, cameras=None, vision=None, camera_state=None):
    app=app_for(world=world,cameras=cameras,vision=vision)
    with ExitStack() as stack:
        if camera_state is not None:
            camera=app.state.resources.cameras
            stack.enter_context(patch.object(camera,'snapshot',side_effect=camera_state))
            stack.enter_context(patch.object(camera,'stream_frame',side_effect=lambda *a:(1,time.time(),b'offline')))
        yield app
