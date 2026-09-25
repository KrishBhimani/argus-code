"""Work-analysis trial (opt-in via ``argus start --work``).

Everything here reads ~/.claude, local git repos and ~/.argus/argus.db
READ-ONLY and writes only ~/.argus/work.db. Delete this package and
work.db to remove the trial; nothing else depends on it.
"""
