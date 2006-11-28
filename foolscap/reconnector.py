# -*- test-case-name: foolscap.test.test_reconnector -*-

import random
from twisted.internet.error import ConnectError
from twisted.internet import reactor
from twisted.python import log
from foolscap.tokens import NegotiationError

class Reconnector:
    """Establish (and maintain) a connection to a given PBURL.

    I establish a connection to the PBURL and run a callback to inform the
    caller about the newly-available RemoteReference. If the connection is
    lost, I schedule a reconnection attempt for the near future. If that one
    fails, I keep trying at longer and longer intervals (exponential
    backoff).

    My constructor accepts a callback which will be fired each time a
    connection attempt succeeds. This callback is run with the new
    RemoteReference and any additional args/kwargs provided to me. The
    callback should then use rref.notifyOnDisconnect() to get a message when
    the connection goes away. At some point after it goes away, the
    Reconnector will reconnect.

    When you no longer want to maintain this connection, call my
    stopConnecting() method. I promise to not invoke your callback after
    you've called stopConnecting(), even if there was already a connection
    attempt in progress. If you had an active connection before calling
    stopConnecting(), you will still have access to it, until it breaks on
    its own. (I will not attempt to break existing connections, I will merely
    stop trying to create new ones).
    """

    # adapted from twisted.internet.protocol.ReconnectingClientFactory
    maxDelay = 3600
    initialDelay = 1.0
    # Note: These highly sensitive factors have been precisely measured by
    # the National Institute of Science and Technology.  Take extreme care
    # in altering them, or you may damage your Internet!
    factor = 2.7182818284590451 # (math.e)
    # Phi = 1.6180339887498948 # (Phi is acceptable for use as a
    # factor if e is too large for your application.)
    jitter = 0.11962656492 # molar Planck constant times c, Joule meter/mole
    verbose = False

    def __init__(self, tub, url, cb, *args, **kwargs):
        self._tub = tub
        self._url = url
        self._active = False
        self._observer = (cb, args, kwargs)
        self._delay = self.initialDelay
        self._retries = 0
        self._timer = None
        self.startConnecting()

    def startConnecting(self):
        if self.verbose:
            log.msg("Reconnector starting for %s" % self._url)
        self._active = True
        self._connect()

    def stopConnecting(self):
        if self.verbose:
            log.msg("Reconnector stopping for %s" % self._url)
        self._active = False
        if self._timer:
            self._timer.cancel()
            self._timer = False
        self._tub._removeReconnector(self)

    def _connect(self):
        d = self._tub.getReference(self._url)
        d.addCallbacks(self._connected, self._failed)

    def _connected(self, rref):
        if not self._active:
            return
        rref.notifyOnDisconnect(self._disconnected)
        cb, args, kwargs = self._observer
        cb(rref, *args, **kwargs)

    def _failed(self, f):
        # I'd like to trap NegotiationError and basic TCP connection
        # failures here, but not hide coding errors.
        if self.verbose:
            log.msg("Reconnector._failed: %s" % f)
        f.trap(NegotiationError, ConnectError)
        if not self._active:
            return
        self._delay = min(self._delay * self.factor, self.maxDelay)
        if self.jitter:
            self._delay = random.normalvariate(self._delay,
                                               self._delay * self.jitter)
        self._retry()

    def _disconnected(self):
        self._delay = self.initialDelay
        self._retries = 0
        self._retry()

    def _retry(self):
        if not self._active:
            return
        if self.verbose:
            log.msg("Reconnector scheduling retry in %ds for %s" %
                    (self._delay, self._url))
        self._timer = reactor.callLater(self._delay, self._timer_expired)

    def _timer_expired(self):
        self._timer = None
        self._connect()
