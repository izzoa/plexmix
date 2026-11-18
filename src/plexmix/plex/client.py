from plexapi.server import PlexServer  # type: ignore[import-untyped]
from plexapi.exceptions import Unauthorized, NotFound, BadRequest  # type: ignore[import-untyped]
from plexapi.library import MusicSection  # type: ignore[import-untyped]
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Generator, List, Optional, Tuple, TypeVar
import logging
import time

from ..database.models import Artist, Album, Track
from ..utils.redaction import (
    MASK,
    mask_credentials_of,
    mask_url_credentials,
    mask_urls_in_text,
)

logger = logging.getLogger(__name__)

# A shorter "token" is not replaced in error text: it would garble the text and hide nothing.
_MIN_TOKEN_LENGTH_TO_REPLACE = 4

_INVALID_TOKEN_MESSAGE = (
    "Plex token contains invalid characters (a space, a line break or a non-ASCII "
    "character). Enter it again."
)

# The HTTP library's errors for an address it cannot use. Their text quotes the
# address, or the part of it that was read as host and port, and that part can be a
# user name and the start of a password with no "@" for the masking to find. So
# these errors are reported by one fixed sentence. Matched by name: the client does
# not import the HTTP library.
_ADDRESS_ERROR_NAMES = frozenset(
    {"InvalidURL", "InvalidSchema", "MissingSchema", "LocationParseError", "LocationValueError"}
)
_ADDRESS_ERROR_PACKAGES = frozenset({"requests", "urllib3", "http"})
_INVALID_ADDRESS_TEXT = (
    "the Plex server address is not a valid URL (it must start with http:// or https://, "
    "and a special character in a password inside it must be percent-encoded)"
)


def _is_address_error(error: BaseException) -> bool:
    """True when ``error`` is the HTTP library saying the address cannot be used."""
    kind = type(error)
    return (
        kind.__name__ in _ADDRESS_ERROR_NAMES
        and kind.__module__.split(".", 1)[0] in _ADDRESS_ERROR_PACKAGES
    )


def _token_can_be_sent(token: str) -> bool:
    """True when every character of ``token`` can go into an HTTP header.

    Expects the ends already stripped. Anything outside printable ASCII without
    spaces (0x21 to 0x7E) makes the HTTP library refuse the header, and its error
    quotes the whole value. This is the rule ``validate_api_key`` applies to API keys.
    """
    return all(0x21 <= ord(ch) <= 0x7E for ch in token)


# The three kinds of item a sync reads, in reading order. They key the count tables
# of ``PlexReadReport`` and name the kind in ``PlexReadInterrupted``.
READ_KINDS: Tuple[str, ...] = ("artists", "albums", "tracks")

# The music library call that lists each kind.
_LISTING_CALLS = {"artists": "searchArtists", "albums": "searchAlbums", "tracks": "searchTracks"}

# A read that fails with a connection-class error is tried again once, after this pause.
_READ_RETRY_DELAY_SECONDS = 1.0

# Per kind, this many item failures (and this many retries) are logged as warnings;
# later ones go to the debug level. A summary line follows either way.
_MAX_ITEM_WARNINGS = 5

_ItemT = TypeVar("_ItemT")


def _zero_counts() -> Dict[str, int]:
    return {kind: 0 for kind in READ_KINDS}


def _listed_key(item: Any) -> str:
    """The rating key of one listed item, as the text the database stores.

    A listing always carries the key, so reading it sends no request. An item
    without one cannot be matched to a database row. That makes the whole listing
    unusable, because the row would look as if Plex no longer listed it.
    """
    key = item.ratingKey
    if key is None or not str(key).strip():
        raise ValueError("a listed item has no rating key")
    return str(key)


@dataclass
class PlexReadReport:
    """What Plex listed, kept apart from what could be read.

    Only the listing can say that a track is gone from Plex. ``listed_track_keys``
    holds the rating key of every track in the listing, whether or not its details
    could be read afterwards. The three tables are keyed by kind (``READ_KINDS``):
    ``listed`` counts the items in the listing, ``failed`` the items whose details
    could not be read, and ``skipped`` the items that were read but are unusable
    (an empty title).
    """

    listed_track_keys: FrozenSet[str] = frozenset()
    listed: Dict[str, int] = field(default_factory=_zero_counts)
    failed: Dict[str, int] = field(default_factory=_zero_counts)
    skipped: Dict[str, int] = field(default_factory=_zero_counts)


class PlexFetchError(Exception):
    """Raised when fetching artists/albums/tracks from Plex fails.

    Distinguishes a failed or impossible fetch (network error, or no music
    library selected) from a genuinely empty library, so the sync engine can
    abort before its deletion phase instead of treating the empty result as
    authoritative and wiping the local database.
    """


class EmptyTitleError(ValueError):
    """A listed item has no title, so it cannot be stored.

    Such an item is counted as skipped, not as a failed read. It is its own class
    because other failures are a ``ValueError`` too (a model that refuses a value,
    for one), and those are failed reads.
    """


class PlexReadInterrupted(PlexFetchError):
    """Plex stopped answering during the reads, or could not list the library.

    The reads are incomplete, so a sync must stop before it writes anything.
    ``kind`` is one of ``READ_KINDS``. ``read`` is the number of items of that kind
    read before the stop. ``listed`` is the size of the listing, or None when the
    listing itself failed. The message is one plain sentence. It never holds a
    server address or any text from Plex's own error.
    """

    def __init__(self, kind: str, read: int = 0, listed: Optional[int] = None) -> None:
        self.kind = kind
        self.read = read
        self.listed = listed
        if listed is None:
            sentence = f"Plex could not list the library's {kind}, so nothing was changed."
        else:
            sentence = (
                f"Plex stopped answering after {read:,} of {listed:,} {kind} were read, "
                "so nothing was changed."
            )
        super().__init__(sentence)


class PlexClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self.token = token
        self.server: Optional[PlexServer] = None
        self.music_library: Optional[MusicSection] = None
        # What the last listing of each kind held and how its reads went. The sync
        # engine removes a track only when this listing leaves it out.
        self.read_report = PlexReadReport()
        # Why the last ``select_library`` call returned False: "" (it did not fail),
        # "not_found", "unreachable" or "error". See ``select_library``.
        self.last_select_failure = ""

    def _safe_error_text(self, error: BaseException) -> str:
        """Return the text of ``error`` in a form that is safe to log or show.

        PlexAPI puts the request address into its error text, and an HTTP library
        can quote a header value. So this client's own token is replaced, as it is
        and in its escaped (``repr``) form, and credentials inside addresses are
        masked. The credentials of this client's own address are replaced by value
        as well, because an address that was read the wrong way is quoted in pieces.
        Every log call and message in this class that includes exception text goes
        through here.
        """
        if _is_address_error(error):
            return _INVALID_ADDRESS_TEXT
        text = str(error)
        token = self.token if isinstance(self.token, str) else ""
        forms: set[str] = set()
        for value in (token, token.strip()):
            forms.update((value, repr(value)[1:-1], ascii(value)[1:-1]))
        # The token goes first: masking an address could change part of a quoted
        # token and leave the rest readable. Longest form first, so a form that
        # contains a shorter one is replaced whole.
        for form in sorted(forms, key=len, reverse=True):
            if len(form) >= _MIN_TOKEN_LENGTH_TO_REPLACE:
                text = text.replace(form, MASK)
        return mask_urls_in_text(mask_credentials_of(self.url, text))

    def _listing_error(self, kind: str, error: BaseException) -> PlexReadInterrupted:
        """Log a failed listing and return the error to raise in place of PlexAPI's.

        PlexAPI's own error holds the full server address. Its text goes to the log
        only, in its safe form. The caller raises the returned error ``from None``:
        one plain sentence, which tells the sync engine to stop before it writes.
        """
        logger.error(f"Failed to retrieve {kind}: {self._safe_error_text(error)}")
        return PlexReadInterrupted(kind, read=0, listed=None)

    def _list_items(self, kind: str, with_keys: bool = False) -> Tuple[List[Any], FrozenSet[str]]:
        """Ask Plex for the listing of one kind, and for the listed keys on request.

        Reads no item details. Raises ``PlexReadInterrupted`` when Plex cannot give
        the listing or, with ``with_keys``, when a listed item has no rating key.
        """
        if not self.music_library:
            raise PlexFetchError("No music library selected")

        try:
            items = list(getattr(self.music_library, _LISTING_CALLS[kind])())
            keys = frozenset(_listed_key(item) for item in items) if with_keys else frozenset()
        except Exception as e:
            raise self._listing_error(kind, e) from None

        logger.info(f"Found {len(items)} {kind}")
        return items, keys

    def _retry_read(
        self, kind: str, extract: Callable[[Any], _ItemT], item: Any, read: int, listed: int
    ) -> _ItemT:
        """Read one item again, after a pause, when its first read lost the connection.

        A second connection-class failure means Plex has stopped answering: the
        reads end with ``PlexReadInterrupted``. Any other failure of the second
        read is left to the caller, like a failure of a first read.

        The item's details are asked for explicitly (``reload``). PlexAPI marks an
        item as loaded before the request for its details has answered, so after a
        failed request it answers every later read from the listing and sends
        nothing. Reading the item again would then succeed without asking Plex.
        """
        time.sleep(_READ_RETRY_DELAY_SECONDS)
        try:
            reload_item = getattr(item, "reload", None)
            if callable(reload_item):
                reload_item()
            return extract(item)
        except OSError as e:
            logger.error(
                f"Plex stopped answering after {read} of {listed} {kind} were read: "
                f"{self._safe_error_text(e)}"
            )
            raise PlexReadInterrupted(kind, read=read, listed=listed) from None

    def _read_all(
        self, kind: str, extract: Callable[[Any], _ItemT], batch_size: int
    ) -> Generator[List[_ItemT], None, None]:
        """List one kind, then read every listed item and yield the results in batches.

        The listing is complete, and recorded in ``read_report``, before the first
        item is read. A read that fails with a connection-class error (``OSError``
        and its subclasses, which covers every error of the HTTP library) is tried
        again once; a second such failure ends the reads (see ``_retry_read``). An
        item with an empty title (``EmptyTitleError``) is counted as skipped. An
        item whose read fails for any other reason is counted as failed, and the
        reads go on. That includes any other ``ValueError``, such as a detail that
        the model refuses.
        """
        singular = kind[:-1]
        report = self.read_report
        report.listed[kind] = report.failed[kind] = report.skipped[kind] = 0
        if kind == "tracks":
            report.listed_track_keys = frozenset()

        items, keys = self._list_items(kind, with_keys=(kind == "tracks"))
        report.listed[kind] = len(items)
        if kind == "tracks":
            report.listed_track_keys = keys

        read = 0
        retries = 0
        batch: List[_ItemT] = []
        for item in items:
            try:
                try:
                    entry = extract(item)
                except OSError as e:
                    retries += 1
                    logger.log(
                        logging.WARNING if retries <= _MAX_ITEM_WARNINGS else logging.DEBUG,
                        f"Lost the connection while reading {kind} "
                        f"({self._safe_error_text(e)}); trying once more",
                    )
                    entry = self._retry_read(kind, extract, item, read, len(items))
            except PlexFetchError:
                # Plex stopped answering: passed on as it is, not counted and not wrapped.
                raise
            except EmptyTitleError as e:
                report.skipped[kind] += 1
                logger.debug(f"Skipping {singular}: {self._safe_error_text(e)}")
                continue
            except Exception as e:
                report.failed[kind] += 1
                logger.log(
                    logging.WARNING if report.failed[kind] <= _MAX_ITEM_WARNINGS else logging.DEBUG,
                    f"Failed to extract {singular} metadata: {self._safe_error_text(e)}",
                )
                continue

            read += 1
            batch.append(entry)
            if len(batch) >= batch_size:
                yield batch
                batch = []

        if batch:
            yield batch

        failed, skipped = report.failed[kind], report.skipped[kind]
        logger.log(
            logging.WARNING if failed else logging.INFO,
            f"Read {read} of {len(items)} {kind} from Plex: {failed} could not be read, "
            f"{skipped} skipped (empty title)",
        )

    def connect(self) -> bool:
        max_retries = 3
        retry_delay = 1

        # Clean the token (remove any whitespace)
        cleaned_token = self.token.strip() if self.token else ""

        # Refuse a token that cannot be sent before any request is made.
        if not _token_can_be_sent(cleaned_token):
            logger.error(_INVALID_TOKEN_MESSAGE)
            return False

        for attempt in range(max_retries):
            try:
                self.server = PlexServer(self.url, cleaned_token)
                logger.info(f"Connected to Plex server: {self.server.friendlyName}")
                return True
            except Unauthorized:
                logger.error("Plex authentication failed: Invalid token")
                return False
            except BadRequest as e:
                logger.error(f"Bad request to Plex server: {self._safe_error_text(e)}")
                logger.error("This usually means:")
                logger.error("  1. The Plex token may be invalid or incorrectly formatted")
                logger.error("  2. The server URL may be incorrect")
                logger.error("  3. Your Plex server may require secure connections (try https://)")
                logger.error(f"Server URL used: {mask_url_credentials(self.url)}")
                return False
            except Exception as e:
                error_text = self._safe_error_text(e)
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Connection attempt {attempt + 1} failed: {error_text}. Retrying..."
                    )
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    logger.error(
                        f"Failed to connect to Plex server after {max_retries} attempts: "
                        f"{error_text}"
                    )
                    return False

        return False

    def test_connection(self) -> bool:
        try:
            if self.server is None:
                return self.connect()
            self.server.library.sections()
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {self._safe_error_text(e)}")
            return False

    def validate_token(self) -> tuple[bool, str]:
        """Validate the current Plex token and return (valid, message).

        Use this as a pre-flight check before expensive operations like sync.
        Returns a tuple of (is_valid, human_readable_message).
        """
        if not self.token or not self.token.strip():
            return False, "Plex token is empty. Configure it in settings or via CLI."

        cleaned_token = self.token.strip()
        # Refuse a token that cannot be sent before any request is made.
        if not _token_can_be_sent(cleaned_token):
            return False, _INVALID_TOKEN_MESSAGE

        try:
            server = PlexServer(self.url, cleaned_token)
            name = server.friendlyName
            return True, f"Connected to {name}"
        except Unauthorized:
            return False, "Plex token is invalid or expired. Please update your token."
        except BadRequest as e:
            return False, (
                f"Bad request: {self._safe_error_text(e)}. Check server URL and token format."
            )
        except Exception as e:
            return False, f"Cannot reach Plex server: {self._safe_error_text(e)}"

    def get_music_libraries(self) -> List[str]:
        if not self.server:
            if not self.connect():
                return []

        try:
            assert self.server is not None
            sections = self.server.library.sections()
            music_sections = [section.title for section in sections if section.type == "artist"]
            logger.info(f"Found {len(music_sections)} music libraries")
            return music_sections
        except Exception as e:
            logger.error(f"Failed to retrieve music libraries: {self._safe_error_text(e)}")
            return []

    def select_library(self, name_or_index: str | int) -> bool:
        """Select the music library to read, by its name or by its place in the list.

        Returns False for every failure. ``last_select_failure`` then says why, and
        it is "" after a call that selected a library:

        - "not_found": Plex has no library with that name, or the list of music
          libraries has no entry at that place. With an index this does not prove
          that Plex answered: ``get_music_libraries`` returns an empty list when it
          could not ask, too.
        - "unreachable": the connection was lost or timed out (``OSError`` and its
          subclasses, which covers every connection error of the HTTP library), so
          nothing is known about the library.
        - "error": anything else, including a failed ``connect`` and an address the
          HTTP library cannot use.
        """
        self.last_select_failure = ""
        if not self.server:
            if not self.connect():
                self.last_select_failure = "error"
                return False

        try:
            if isinstance(name_or_index, int):
                libraries = self.get_music_libraries()
                if 0 <= name_or_index < len(libraries):
                    library_name = libraries[name_or_index]
                else:
                    logger.error(f"Invalid library index: {name_or_index}")
                    self.last_select_failure = "not_found"
                    return False
            else:
                library_name = name_or_index

            assert self.server is not None
            self.music_library = self.server.library.section(library_name)
            logger.info(f"Selected music library: {library_name}")
            return True
        except NotFound:
            logger.error(f"Music library not found: {name_or_index}")
            self.last_select_failure = "not_found"
            return False
        except Exception as e:
            logger.error(f"Failed to select library: {self._safe_error_text(e)}")
            lost_connection = isinstance(e, OSError) and not _is_address_error(e)
            self.last_select_failure = "unreachable" if lost_connection else "error"
            return False

    def get_all_artists(self, batch_size: int = 100) -> Generator[List[Artist], None, None]:
        yield from self._read_all("artists", self.extract_artist_metadata, batch_size)

    def get_all_albums(self, batch_size: int = 100) -> Generator[List[Album], None, None]:
        yield from self._read_all("albums", self.extract_album_metadata, batch_size)

    def get_all_tracks(self, batch_size: int = 100) -> Generator[List[Track], None, None]:
        yield from self._read_all("tracks", self.extract_track_metadata, batch_size)

    def list_track_keys(self) -> FrozenSet[str]:
        """List the library's tracks and return their rating keys.

        Reads no item details and leaves ``read_report`` as it is. Raises
        ``PlexReadInterrupted`` when Plex cannot give the listing.
        """
        _items, keys = self._list_items("tracks", with_keys=True)
        return keys

    def extract_artist_metadata(self, plex_artist: Any) -> Artist:
        if not plex_artist.title or not plex_artist.title.strip():
            raise EmptyTitleError(f"Artist has empty name (key: {plex_artist.ratingKey})")

        genres = (
            [genre.tag for genre in plex_artist.genres] if hasattr(plex_artist, "genres") else []
        )
        genre_str = ", ".join(genres) if genres else None

        return Artist(
            plex_key=str(plex_artist.ratingKey),
            name=plex_artist.title,
            genre=genre_str,
            bio=plex_artist.summary if hasattr(plex_artist, "summary") else None,
        )

    def extract_album_metadata(self, plex_album: Any) -> Album:
        if not plex_album.title or not plex_album.title.strip():
            raise EmptyTitleError(f"Album has empty title (key: {plex_album.ratingKey})")

        genres = [genre.tag for genre in plex_album.genres] if hasattr(plex_album, "genres") else []
        genre_str = ", ".join(genres) if genres else None

        # Use parentRatingKey (available without extra API call) instead of plex_album.artist()
        artist_key = None
        if hasattr(plex_album, "parentRatingKey") and plex_album.parentRatingKey:
            artist_key = str(plex_album.parentRatingKey)

        album = Album(
            plex_key=str(plex_album.ratingKey),
            title=plex_album.title,
            artist_id=0,  # Will be set in sync based on artist rating key
            year=plex_album.year if hasattr(plex_album, "year") else None,
            genre=genre_str,
            cover_art_url=plex_album.thumb if hasattr(plex_album, "thumb") else None,
        )

        # Store artist rating key as temporary attribute (not part of model schema)
        album.__dict__["_artist_key"] = artist_key

        return album

    def extract_track_metadata(self, plex_track: Any) -> Track:
        if not plex_track.title or not plex_track.title.strip():
            raise EmptyTitleError(f"Track has empty title (key: {plex_track.ratingKey})")

        genres = [genre.tag for genre in plex_track.genres] if hasattr(plex_track, "genres") else []
        genre_str = ", ".join(genres) if genres else None

        # Use grandparentRatingKey (artist) and parentRatingKey (album) — available
        # without extra API calls, unlike plex_track.artist() / plex_track.album()
        artist_key = None
        if hasattr(plex_track, "grandparentRatingKey") and plex_track.grandparentRatingKey:
            artist_key = str(plex_track.grandparentRatingKey)
        album_key = None
        if hasattr(plex_track, "parentRatingKey") and plex_track.parentRatingKey:
            album_key = str(plex_track.parentRatingKey)

        track = Track(
            plex_key=str(plex_track.ratingKey),
            title=plex_track.title,
            artist_id=0,  # Will be set in sync based on artist rating key
            album_id=0,  # Will be set in sync based on album rating key
            duration_ms=plex_track.duration if hasattr(plex_track, "duration") else None,
            genre=genre_str,
            year=plex_track.year if hasattr(plex_track, "year") else None,
            # Plex stores userRating on a 0-10 scale (10 = 5 stars); PlexMix
            # stores ratings on a 0-5 scale, so convert at the boundary. Passing
            # the raw 0-10 value fails Track's 0-5 validator, which previously
            # caused rated tracks to be skipped and then deleted on sync.
            rating=(
                plex_track.userRating / 2.0
                if getattr(plex_track, "userRating", None) is not None
                else None
            ),
            play_count=plex_track.viewCount if hasattr(plex_track, "viewCount") else 0,
            last_played=plex_track.lastViewedAt if hasattr(plex_track, "lastViewedAt") else None,
            file_path=self._extract_file_path(plex_track),
        )

        # Store rating keys as temporary attributes (not part of model schema)
        track.__dict__["_artist_key"] = artist_key
        track.__dict__["_album_key"] = album_key

        return track

    @staticmethod
    def _extract_file_path(plex_track: Any) -> Optional[str]:
        """Extract the file path from a Plex track's media parts."""
        try:
            if hasattr(plex_track, "media") and plex_track.media:
                parts = plex_track.media[0].parts if plex_track.media[0].parts else []
                if parts:
                    file_path: Optional[str] = parts[0].file
                    return file_path
        except Exception:
            pass
        return None

    def create_playlist(
        self, name: str, track_plex_keys: List[str], description: Optional[str] = None
    ) -> Any:
        if not self.music_library:
            logger.error("No music library selected")
            return None

        try:
            tracks = []
            for plex_key in track_plex_keys:
                try:
                    # fetchItem expects an integer rating key, not a string
                    track = self.music_library.fetchItem(int(plex_key))
                    tracks.append(track)
                except Exception as e:
                    logger.warning(f"Failed to fetch track {plex_key}: {self._safe_error_text(e)}")

            if not tracks:
                logger.error("No valid tracks found for playlist")
                return None

            if not self.server:
                logger.error("Not connected to Plex server")
                return None

            playlist = self.server.createPlaylist(title=name, items=tracks)

            if description and hasattr(playlist, "editSummary"):
                # Setting the description is best-effort: a failure here must not
                # discard the already-created playlist (which would orphan it in
                # Plex while the caller sees None and assumes creation failed).
                try:
                    playlist.editSummary(description)
                except Exception as e:
                    logger.warning(
                        "Created playlist but failed to set its description: %s",
                        self._safe_error_text(e),
                    )

            logger.info(f"Created playlist '{name}' with {len(tracks)} tracks")
            return playlist

        except Exception as e:
            logger.error(f"Failed to create playlist: {self._safe_error_text(e)}")
            return None
