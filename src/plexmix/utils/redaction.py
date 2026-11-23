"""Hide credentials in server addresses before they are printed or logged.

A Plex address can carry a user name and password (``http://user:password@host``),
and PlexAPI repeats the address in its error texts. Both helpers work on the text
pattern, not on URL parsing: an address written without ``http://`` does not parse
as a URL, and it still has to be masked.

Only what is shown is changed. Callers keep using the configured address as it is.
"""

import re
from typing import List, Optional, Tuple

MASK = "***"

# What takes the place of a key value, or of a word that shows part of one.
REDACTED = "[redacted]"

# A key that a provider repeats with its middle hidden ("sk-ab********wxyz") still
# shows both ends. Four stars or more mark such a word; ``MASK`` has three.
_HIDDEN_MIDDLE = "****"
_WHITESPACE_RE = re.compile(r"(\s+)")

# The value of an ``X-Plex-Token=`` query parameter, up to the next parameter.
_TOKEN_PARAMETER_RE = re.compile(r"(X-Plex-Token=)[^&\s#]+", re.IGNORECASE)

# ``user@`` or ``user:password@`` in front of a host, inside a longer text. The
# credentials must not contain whitespace: in a text nothing else says where they
# begin. Every repeat is bounded, so a long text without spaces cannot make the
# search slow.
_CREDENTIALS_RE = re.compile(
    r"""
    (?P<lead>
        # "http://", "https://" or any other scheme. "http" also counts right after
        # a character that cannot start a scheme name ("-http://", "9http://").
        (?:(?<![A-Za-z0-9+.\-])[A-Za-z][A-Za-z0-9+.\-]{0,31}|https?)://
      |
        https?(?::/{1,4}|/{2,4})           # mistyped: "http:/", "http:///", "http//"
      |
        (?<!\S)['"(<{]{0,8}//              # "//" at the start of a word
      |
        (?<!\S)['"(<{]{0,8}(?!['"(<{])     # or the start of a word, after any quote
    )
    (?:
        # "user" or "user:password". Greedy, so the password may hold ":" or "@".
        [^\s/?#]{1,1024}
      |
        # The same with "/", "?" or "#" in the password. Not tried when a plain
        # "host" or "host:port" comes first, so "host:32400/a@b" is left alone.
        (?!(?:\[[^\]\s]{0,255}\]|[^\s/?#@:\[\]]{0,255})(?::\d{0,5})?[/?#])
        \S{1,1024}
    )
    @
    (?=[\w\[])                             # a host follows
    """,
    re.VERBOSE,
)

# What can stand in front of the credentials of one address: nothing, a scheme
# ("http://", any name), a mistyped "http:/", "http:///" or "http//", or "//".
_ADDRESS_LEAD_RE = re.compile(
    r"\s*(?:[A-Za-z][A-Za-z0-9+.\-]{0,31}:/{2,4}|https?(?::/|/{2,4})|/{2,4})?"
)

# The "@" that ends the credentials of one address: a host follows it, or nothing does.
_CREDENTIALS_END_RE = re.compile(r"@(?=[\w\[/?#]|\s*$)")

# "host" or "host:port" and nothing else. After one of these, a "/" starts the path.
_PLAIN_HOST_RE = re.compile(r"(?:\[[^\]\s]*\]|[^\s/?#@:\[\]]*)(?::\d{1,5})?")

# Where an HTTP library can cut credentials when it reads an address as a URL.
_CREDENTIALS_PIECE_SPLIT_RE = re.compile(r"[:/?#\\\s]+")

# A shorter piece is not replaced by value: it would garble a text and hide nothing.
_MIN_PIECE_LENGTH = 4


def _mask_credentials(match: re.Match[str]) -> str:
    return f"{match.group('lead')}{MASK}@"


def _last_credentials_end(text: str) -> int:
    """Index of the last ``@`` in ``text`` that can end credentials, or -1."""
    ends = [match.start() for match in _CREDENTIALS_END_RE.finditer(text)]
    return ends[-1] if ends else -1


def _split_single_address(address: str) -> Optional[Tuple[str, str, str]]:
    """Split one address into (scheme, credentials, rest), or None without credentials.

    One address is known to be one, so the rule can be stricter than the one for a
    text: everything between the scheme and the last ``@`` in front of the host is
    credentials, whatever it holds (a space, a tab, a line break, any length).
    """
    lead_match = _ADDRESS_LEAD_RE.match(address)
    lead = lead_match.group(0) if lead_match else ""
    rest = address[len(lead) :]
    path_start = next((i for i, ch in enumerate(rest) if ch in "/?#"), len(rest))
    before_path = rest[:path_start]
    end = _last_credentials_end(before_path)
    if end < 0 and not _PLAIN_HOST_RE.fullmatch(before_path):
        # Not a plain "host" or "host:port" in front of the first "/", "?" or "#",
        # so that character belongs to a password and the host comes later.
        end = _last_credentials_end(rest)
    if end <= 0:
        return None
    return lead, rest[:end], rest[end:]


def mask_url_credentials(address: str) -> str:
    """Return ``address`` with an embedded user name and password replaced by ``***``.

    ``http://user:password@host:32400`` becomes ``http://***@host:32400``. The scheme
    is optional (``user:password@host`` becomes ``***@host``) and may be mistyped
    (``http:/``, ``http//``). The password is optional too and may hold any
    character, a space included. The value of an ``X-Plex-Token=`` parameter is
    replaced as well. Host, port and path are kept. An address without credentials,
    and a string that is not an address, are returned unchanged.

    Pass one address, not a text: for a text use ``mask_urls_in_text``.
    """
    if not address or not isinstance(address, str):
        # Nothing to show, or not text at all: a display helper must not fail on it.
        return address
    address = _TOKEN_PARAMETER_RE.sub(rf"\g<1>{MASK}", address)
    if "@" not in address:
        return address
    parts = _split_single_address(address)
    if parts is not None:
        lead, _credentials, rest = parts
        return f"{lead}{MASK}{rest}"
    return _CREDENTIALS_RE.sub(_mask_credentials, address)


def mask_urls_in_text(text: str) -> str:
    """Return ``text`` with the credentials of every address in it replaced by ``***``.

    Also replaces the value of any ``X-Plex-Token=`` parameter. Use it for error text
    that came from Plex or an HTTP library before logging or showing it. An e-mail
    address has the same shape as ``user@host`` and is masked too.
    """
    if not text:
        return text
    text = _TOKEN_PARAMETER_RE.sub(rf"\g<1>{MASK}", text)
    if "@" not in text:
        return text
    return _CREDENTIALS_RE.sub(_mask_credentials, text)


def redact_partly_hidden_keys(text: str) -> str:
    """Return ``text`` with every word that shows a key with its middle hidden replaced.

    Some providers repeat the key they were sent in an error text as
    ``sk-ab********wxyz``. Both ends are still readable, so the whole word becomes
    ``[redacted]``. Everything else, the white space included, is returned as it
    was. The text is read word by word, so a long text without spaces takes no
    longer than a short one. The three stars that ``mask_urls_in_text`` writes are
    not such a word and stay.
    """
    if not text or _HIDDEN_MIDDLE not in text:
        return text
    return "".join(
        REDACTED if _HIDDEN_MIDDLE in piece else piece for piece in _WHITESPACE_RE.split(text)
    )


def mask_credentials_of(address: str, text: str) -> str:
    """Return ``text`` without the credentials of ``address``, whole or in pieces.

    ``mask_urls_in_text`` finds credentials by their place in front of an ``@``. A
    password that holds ``/``, ``?``, ``#`` or a backslash makes an HTTP library read
    the address another way: what stands in front of that character becomes "host
    and port", the rest becomes the path, and an error text can quote either part
    without the ``@``. So the credentials of the one address the caller knows are
    replaced by value: as a whole, the password, and each piece of them between
    those characters.

    The password and the pieces are replaced only where they stand as a whole word,
    and not at all when they are also part of what the address shows (its host, for
    example), so ordinary text stays readable.
    """
    if not text or not isinstance(address, str):
        return text
    parts = _split_single_address(address)
    if parts is None:
        return text
    lead, credentials, rest = parts
    # "user:password", and "user:password-start": what a URL parser takes for host
    # and port when the password holds one of the characters above.
    joined: List[str] = [credentials] + [
        credentials[: match.start()]
        for match in _CREDENTIALS_PIECE_SPLIT_RE.finditer(credentials)
        if ":" in credentials[: match.start()]
    ]
    for form in sorted(set(joined), key=len, reverse=True):
        if len(form) >= _MIN_PIECE_LENGTH:
            text = text.replace(form, MASK)
    visible = f"{lead}{rest}"
    _user, _colon, password = credentials.partition(":")
    words = {password, *_CREDENTIALS_PIECE_SPLIT_RE.split(credentials)}
    for word in sorted(words, key=len, reverse=True):
        if len(word) >= _MIN_PIECE_LENGTH and word not in visible:
            text = re.sub(rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])", MASK, text)
    return text
