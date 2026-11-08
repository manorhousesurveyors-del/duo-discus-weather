"""Optional assistant I/O has its own workers, separate from weather and OGN."""
from functools import lru_cache
from weather_engine import ResourceStore

@lru_cache(maxsize=1)
def jobs():return ResourceStore(workers=2,max_entries=24)


def connection(fingerprint,auth,credential,*,refresh=False):
    from app_cache import gemini_discovery
    from gemini_rest import Connection,GeminiError
    from uk_map_grid import grid_progress
    if not credential:return Connection((),auth,'No credential entered · local forecast assistant')
    key=('models',fingerprint,auth)
    if refresh:
        gemini_discovery.clear(fingerprint,auth,_credential=credential)
        jobs().invalidate(key)
    def request():
        return jobs().request(key,lambda:gemini_discovery(fingerprint,auth,_credential=credential),
                              accept=lambda value:bool(value.models) and not value.error,ttl=3600)
    future=request()
    if not future.done():
        grid_progress(future,'Checking the assistant connection in the background.')
        return Connection((),auth,'Checking connection in the background')
    try:return future.result()
    except GeminiError as error:
        grid_progress(future,'Assistant connection recovery is scheduled.',retry=request)
        return Connection((),auth,str(error))
