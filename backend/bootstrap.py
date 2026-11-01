"""Application resource lifecycle; HTTP routes never own device lifetimes."""
import asyncio


class ApplicationResources:
    def __init__(self, *, world, vision, cameras, lease, pipeline=None,
                 programs=None, scenes=None, ik_previews=None, data_dir=None):
        self.world, self.vision, self.cameras, self.lease = world, vision, cameras, lease
        self.pipeline = pipeline
        self.programs,self.scenes,self.ik_previews = programs,scenes,ik_previews
        self.data_dir = data_dir
        self.remote = None

    async def start(self):
        self.lease.acquire()
        try:
            journal=getattr(self.world,'run_journal',None)
            if journal is not None:await journal.start()
            await self.world.start()
            await self.vision.start()
            if self.pipeline is not None:
                await self.pipeline.start()
            if self.remote is not None:
                await self.remote.start()
        except BaseException as initiating_error:
            # A failed start may already own workers or capture handles.
            # Cleanup failures must not replace the initiating exception.
            try:
                await self.close()
            except BaseException as cleanup_error:
                initiating_error.add_note(f"Resource cleanup also failed: {cleanup_error!r}")
            raise

    async def close(self):
        try:
            try:
                if self.remote is not None:
                    await self.remote.close()
            finally:
                # Revoke motion before waiting for blocking camera cleanup.
                try:
                    await self.world.stop()
                finally:
                    journal=getattr(self.world,'run_journal',None)
                    if journal is not None:await journal.close()
        finally:
            try:
                try:
                    try:
                        if self.pipeline is not None:
                            await self.pipeline.close()
                    finally:
                        await self.vision.close()
                finally:
                    await asyncio.to_thread(self.cameras.close)
            finally:
                self.lease.release()
