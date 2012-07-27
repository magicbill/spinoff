class _Values(list):
    pass


def _is_collect(pattern):
    return (isinstance(pattern, Matcher) and not pattern.ignore)


def match(pattern, subject, flatten=True):
    # XXX: try to optimize this function
    def _match(pattern, subject, success):
        if not isinstance(pattern, tuple):
            values = _Values([subject] if _is_collect(pattern) else [])
            return (success and pattern == subject, values)
        else:
            values = _Values()
            subject_is_tuple = isinstance(subject, tuple)

            for subpattern in pattern:
                success, subvalues = _match(subpattern, subject[0] if subject_is_tuple and subject else None, success)

                assert isinstance(subvalues, _Values)
                values.extend(subvalues)

                subject = subject[1:] if subject_is_tuple and subject else None

            # if not all of the subject has been consumed, the match has failed:
            if subject:
                success = False

            return success, values

    success, values = _match(pattern, subject, True)
    assert isinstance(values, _Values)

    return ((success, tuple(values))
            if not flatten else
            (success if not values else (success,) + tuple(values)))


class _Marker(object):
    def __repr__(self):
        return self.__str__()

    def clone(self):
        return type(self)()


class Matcher(_Marker):
    ignore = False

    def __req__(self, x):
        return self.__eq__(x)


class _ANY(Matcher):
    name = 'ANY'

    def __eq__(self, x):
        return True
ANY = _ANY()


def IGNORE(x):
    if isinstance(x, Matcher):
        x = x.clone()
        x.ignore = True
    return x


class IS_INSTANCE(Matcher):
    def __init__(self, t):
        self.t = t

    def __eq__(self, x):
        return isinstance(x, self.t)

    def __str__(self):
        return 'IS_INSTANCE(%s)' % self.t

    def clone(self):
        return type(self)(self.t)


class MATCH(Matcher):
    def __init__(self, fn):
        self.fn = fn

    def __eq__(self, x):
        return self.fn(x)

    def __str__(self):
        return 'MATCH(%s)' % self.fn

    def clone(self):
        return type(self)(self.fn)


class NOT(Matcher):
    def __init__(self, matcher):
        self.matcher = matcher

    def __eq__(self, x):
        return self.matcher != x

    def __str__(self):
        return 'NOT(%s)' % self.matcher

    def clone(self):
        return type(self)(self.matcher)


class IF(Matcher):
    def __init__(self, cond, pattern):
        self.cond = cond
        self.pattern = pattern

    def __eq__(self, x):
        return self.cond() and x == self.pattern

    def __str__(self):
        # TODO: find a way to re-build the original source code from self.cond.func_code.co_code
        return 'IF(%s, %s)' % (self.cond, self.pattern)

    def clone(self):
        return type(self)(self.cond, self.pattern)