from __future__ import print_function

import sys
import warnings
import types
from collections import defaultdict

from twisted.application import service
from twisted.application.service import Service
from twisted.python import log
from twisted.python.failure import Failure
from twisted.internet.defer import DeferredQueue
from unnamedframework.util.async import combine
from unnamedframework.util.meta import selfdocumenting
from zope.interface import Interface, implements
from unnamedframework.util.python import combomethod
from unnamedframework.util.microprocess import MicroProcess, microprocess


__all__ = ['IActor', 'IProducer', 'IConsumer', 'Actor', 'Pipeline', 'Application', 'NoRoute', 'RoutingException', 'InterfaceException', 'ActorsAsService']


class NoRoute(Exception):
    pass


class RoutingException(Exception):
    pass


class InterfaceException(Exception):
    pass


class IProducer(Interface):

    def connect(outbox, (inbox, component)):
        """Connects the `outbox` of this component to one of the `inbox`es of another `component`.

        It is legal to pass in `self` as the value of `component` if needed.

        """


class IConsumer(Interface):

    def deliver(message, inbox='default'):
        """Delivers an incoming `message` into one of the `inbox`es of this component.

        Returns a `Deferred` which will be fired when this component has received the `message`.

        """

    def plugged(inbox, component):
        """Called when something has been plugged into the specified `inbox` of this `IConsumer`.

        (Optional).

        """


class IActor(IProducer, IConsumer):
    pass


class Actor(MicroProcess):
    implements(IActor)

    parent = property(lambda self: self._parent)

    def __init__(self, connections=None, *args, **kwargs):
        super(Actor, self).__init__()
        self._inboxes = defaultdict(lambda: DeferredQueue(backlog=1))
        self._waiting = {}
        self._outboxes = {}
        self._parent = None
        self._children = []

        self._run_args = []
        self._run_kwargs = {}

        if connections:
            for connection in connections.items():
                self.connect(*connection)

    @combomethod
    def spawn(cls_or_self, *args, **kwargs):
        if not isinstance(cls_or_self, Actor):
            cls = cls_or_self
            ret = cls(*args, **kwargs)
            d = ret.start()
            d.addErrback(lambda f: (
                f.printTraceback(sys.stderr),
                ))
            return ret
        else:
            self = cls_or_self
            if 'actor_cls' in kwargs:
                actor_cls = kwargs.pop('actor_cls')
            elif len(args) >= 0:
                actor_cls = args[0]
                args = args[1:]
            else:
                raise TypeError("spawn() requires an actor class to be passed as the "
                                "first argument or actor_cls keyword argument")

            if isinstance(actor_cls, (types.FunctionType, types.MethodType)):
                actor_cls = microprocess(actor_cls)

            def on_result(result):
                if result is not None:
                    warnings.warn("actor returned a value but this value will be lost--"
                                  "send it to the parent explicitly instead")

            child = actor_cls(*args, **kwargs)
            if hasattr(child, '_parent'):
                child._parent = self
            d = child.start()
            self._children.append(child)
            d.addCallback(on_result)
            d.addErrback(lambda f: self.send(inbox='child-errors', message=(child, f.value)))
            d.addBoth(lambda _: self._children.remove(child))
            return child

    def join(self, other):
        return other.d

    def join_children(self):
        return combine([x.d for x in self._children])

    def deliver(self, message, inbox='default'):
        self._inboxes[inbox].put(message)

    send = deliver

    def connect(self, outbox='default', to=None):
        """%(parent_doc)s

        The connection (`to`) can be either a tuple of `(<inbox>, <receiver>)` or just `receiver`, in which case `<inbox>` is
        taken to be `'default'`.

        If no `outbox` is specified, it is taken to be `'default'`, thus:

            `comp_a.connect(to=...)`

        is equivalent to:

            `comp_a.connect('default', ...)`

        and

            `comp_a.connect(to=comp_b)`

        is equivalent to:

            `a.connect('default', ('default', b))`

        and

            `comp_a.connect('outbox', comp_b)`

        is equivalent to:

            `comp_a.connect('outbox', ('default', comp_b))`

        """
        inbox, receiver = (to if isinstance(to, tuple) else ('default', to))
        self._outboxes.setdefault(outbox, []).append((inbox, receiver))
        if hasattr(receiver, 'plugged'):
            receiver.plugged(inbox, self)
        if hasattr(self, 'connected'):
            self.connected(outbox, receiver)
    connect.__doc__ %= {'parent_doc': IActor.getDescriptionFor('connect').getDoc()}

    def plugged(self, inbox, component):
        self._inboxes[inbox]  # leverage defaultdict behaviour

    @selfdocumenting
    def short_circuit(self, outbox, inbox=None):
        if inbox is None:
            inbox = outbox
        self.connect(outbox, (inbox, self))

    def get(self, inbox='default'):
        if inbox not in self._inboxes:
            warnings.warn("Actor %s attempted to get from a non-existent inbox %s" % (repr(self), repr(inbox)))
        return self._inboxes[inbox].get()

    def put(self, message, outbox='default'):
        """Puts a `message` into one of the `outbox`es of this component.

        If the specified `outbox` has not been previously connected to anywhere (see `Actor.connect`), a
        `NoRoute` will be raised, i.e. outgoing messages cannot be queued locally and must immediately be delivered
        to an inbox of another component and be queued there (if/as needed).

        Returns a `Deferred` which will be fired when the messages has been delivered to all connected components.

        """
        if outbox not in self._outboxes:
            raise NoRoute("Actor %s has no connection from outbox %s" % (repr(self), repr(outbox)))

        connections = self._outboxes[outbox]
        for inbox, component in connections:
            component.deliver(message, inbox)

    def _on_complete(self):
        # mark this actor as stopped only when all children have been joined
        ret = self.join_children()
        ret.addCallback(lambda result: super(Actor, self)._on_complete())
        return ret

    def pause(self):
        super(Actor, self).pause()
        for child in self._children:
            if child.is_running:
                child.pause()

    def resume(self):
        super(Actor, self).resume()
        for child in self._children:
            assert child.is_paused
            child.resume()

    def stop(self):
        super(Actor, self).stop()
        for child in self._children:
            child.stop()

    def debug_state(self, name=None):
        for inbox, queue in self._inboxes.items():
            print('*** %s.INBOX %s:' % (name or '', inbox))
            for message, _ in queue.pending:
                print('*** \t%s' % message)

    def inbox(self, inbox):
        return ('default', _Inbox(self, inbox))

    def as_service(self):
        warnings.warn("Actor.as_service is deprecated, use `twistd runactor -a path.to.ActorClass` instead", DeprecationWarning)
        return ActorsAsService([self])


class ActorsAsService(Service):

    def __init__(self, actors):
        warnings.warn("ActorsAsService is deprecated, use `twistd runactor -a path.to.ActorClass` instead", DeprecationWarning)
        self._actors = actors

    def startService(self):
        for x in self._actors:
            x.start()

    def stopService(self):
        return combine([d for d in [x.stop() for x in self._actors] if d])


class ActorRunner(Service):

    def __init__(self, actor):
        self._actor = actor

    def startService(self):
        actor_path = '%s.%s' % (type(self._actor).__module__, type(self._actor).__name__)

        log.msg("running: %s" % actor_path)

        try:
            d = self._actor.start()
        except Exception:
            sys.stderr.write("failed to start: %s\n" % actor_path)
            Failure().printTraceback(file=sys.stderr)
            return

        @d.addBoth
        def finally_(result):
            if isinstance(result, Failure):
                sys.stderr.write("failed: %s\n" % actor_path)
                result.printTraceback(file=sys.stderr)
            else:
                sys.stderr.write("finished: %s\n" % actor_path)

            # os.kill(os.getpid(), signal.SIGKILL)

    def stopService(self):
        if self._actor.is_alive:
            self._actor.stop()


class _Inbox(object):
    implements(IConsumer)

    def __init__(self, actor, inbox):
        self.actor, self.inbox = actor, inbox
        actor.plugged(inbox, self)

    def deliver(self, message, inbox):
        assert inbox == 'default'
        self.actor.deliver(message=message, inbox=self.inbox)


def _normalize_pipe(pipe):
    if not isinstance(pipe, tuple):
        pipe = (pipe, )
    assert len(pipe) <= 3, "A pipe definition is should be a 3-tuple"

    is_box = lambda x: isinstance(x, basestring)

    if len(pipe) == 3:
        assert is_box(pipe[0]), "Left item of a pipe definition should be an inbox name"
        assert is_box(pipe[2]), "Right item of a pipe definition should be an outbox name"
    elif len(pipe) == 1:
        pipe = ('default', pipe[0], 'default')
    else:
        pipe = ('default', ) + pipe if is_box(pipe[1]) else pipe + ('default', )

    assert is_box(pipe[0]) or is_box(pipe[2]), "Left and right item of a pipe definition shuld be box names"
    return pipe


def Pipeline(*pipes):
    """Returns a `Pipeline` that can be used as part of an `Application`.

    A `Pipeline` consists of one ore more pipes.

    A pipe is a connection/link in the pipeline; a pipe connects a
    component to its neighbouring components via inboxes and outboxes;
    the normalized form of a pipe definition is a 3-tuple of the form:

        `(<inbox-name>, <component>, <outbox-name>)`

    where `inbox-name`
    and `outbox-name` should be strings; a pipe definition can
    optionally be shortened to following forms:

        `(<inbox-name>, <component>)`
        `(<component>, <outbox-name>)`
        `(<component>, )`
        `<component>`

    each of which will be normalized, unspecified box names defaulting
    to `'default'`.

    """
    pipes = [_normalize_pipe(pipe) for pipe in pipes]

    for sender, receiver in zip(pipes[:-1], pipes[1:]):
        _, sender, outbox = sender
        inbox, receiver, _ = receiver
        sender.connect(outbox, (inbox, receiver))

    return [pipe[1] for pipe in pipes]


def Application(*pipelines):
    """Returns an application object that can be run using `twistd`.

    An `Application` consists of one or more pipelines.

    """
    services = []
    for pipeline in pipelines:
        # components = [connection[1] for stage in pipeline for connection in stage]
        services.extend(pipeline)

    application = service.Application("DTS Server")
    for s in services:
        s.setServiceParent(application)

    return application


def actor(fn):
    class ret(Actor):
        run = fn
    ret.__name__ = fn.__name__
    return ret