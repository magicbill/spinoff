from spinoff.actor import Actor
from spinoff.util.pattern_matching import ANY, OR
from spinoff.contrib.filetransfer import constants
from spinoff.util.logging import dbg


class Request(Actor):
    def run(self, server, file_id, max_buffer_size=constants.DEFAULT_BUFFER_SIZE):
        handler = None
        client = None
        buf = []
        more_coming = True
        blocked = False
        paused = False

        self.watch(server)
        server << ('request', file_id)
        while True:
            msg = self.get(OR(('chunk', ANY, ANY),
                              'next' if not blocked else object(),
                              ('terminated', ANY)))
            if ('terminated', ANY) == msg:
                dbg("TERM")
                if not client:
                    self.get('next')
                    client = self.sender
                client << 'failed'
            elif msg == 'next':
                if paused:
                    paused = False
                    handler << 'unpause'
                client = self.sender
                if buf:
                    chunk = buf.pop(0)
                    client << ('chunk', chunk, bool(buf) or more_coming)
                else:
                    _, chunk, more_coming = msg = self.get(('chunk', ANY, ANY))
                    client << ('chunk', chunk, more_coming)
            else:
                _, chunk, more_coming = msg
                dbg("got chunk: %db; more_coming? %s" % (len(chunk), "yes" if more_coming else "no"))
                if not more_coming:
                    client << 'stop'
                if not handler:
                    handler = self.sender
                    self.watch(handler)
                buf.append(chunk)
                if sum(len(x) for x in buf) >= max_buffer_size:
                    paused = True
                    handler << 'pause'
