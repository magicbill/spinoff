from twisted.internet.defer import inlineCallbacks
from twisted.trial import unittest

from spinoff.actor.transport.zeromq import ZmqRouter, ZmqDealer
from spinoff.util.async import TimeoutError, sleep, with_timeout
from spinoff.util.testing import assert_not_raises, contain


_wait_msg = lambda d: with_timeout(4.0, d)
_wait_slow_joiners = lambda n=1: sleep(0.001 * n)  # try increasing this if tests fail


ADDR = 'ipc://test'


class RouterDealerTestCase(unittest.TestCase):

    @inlineCallbacks
    def _do_test_router_with_n_dealers(self, n=1):
        with contain(ZmqRouter(ADDR)) as (container, router):
            dealers = []
            for i in range(n):
                dealer = container.spawn(ZmqDealer(ADDR, identity='dude%s' % i))
                dealers.append(dealer)

            yield _wait_slow_joiners(n)

            for dealer in dealers:
                msg = 'PING%s' % i

                router.send(message=(dealer.identity, msg))
                with assert_not_raises(TimeoutError, "should have received a message"):
                    assert msg == (yield container.wait())

    def test_router_with_1_dealer(self):
        return self._do_test_router_with_n_dealers(1)

    def test_router_with_2_dealers(self):
        return self._do_test_router_with_n_dealers(2)

    def test_router_with_3_dealers(self):
        return self._do_test_router_with_n_dealers(3)

    def test_router_with_10_dealers(self):
        return self._do_test_router_with_n_dealers(10)

    def test_release_files(self):
        for i in range(200):
            with contain(ZmqRouter('ipc://whatev')):
                pass
