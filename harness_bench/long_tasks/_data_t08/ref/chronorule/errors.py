"""Error type shared by the whole package (port of src/errors.js)."""

E_PARSE = "E_PARSE"
E_RANGE = "E_RANGE"
E_RULE = "E_RULE"
E_LIMIT = "E_LIMIT"
E_TYPE = "E_TYPE"

CODES = (E_PARSE, E_RANGE, E_RULE, E_LIMIT, E_TYPE)


class ChronoError(ValueError):
    """Every error raised by chronorule carries a stable machine-readable code."""

    def __init__(self, code, message=""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def fail(code, message=""):
    raise ChronoError(code, message)
