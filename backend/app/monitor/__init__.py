"""Nobody finds a problem before we do (docs/monitoring.md).

``signals``: what each process counts (requests, slow endpoints, the pool, the idle timeout).
``connections``: every shop-connection attempt and how it ended.
``rules``: what is wrong right now, as problems, read from the database and Redis.
``notify``: one alert per problem, grouped per account, capped per hour, and "resolved" when it clears.
``digest``: the 09:00 Istanbul summary.
``tick``: the loop that runs them, in the API (so it still runs when the worker does not).
"""
