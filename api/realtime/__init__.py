"""WebSocket transport: the per-process connection registry, the Redis relay
that joins one worker's registry to another's, and the per-socket session loop.

Business logic lives in `api.services`; nothing here decides whether an edit is
allowed, only how it travels.
"""
