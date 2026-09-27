"""New learning window: one React page hosted in Qt WebEngine (ARCH-01).

``bridge`` carries requests/events between the page and Python, ``services``
owns account-scoped backends, ``handlers`` maps bridge methods to them and
``window`` is the Qt window the account shell talks to.
"""
