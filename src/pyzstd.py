
__all__ = ('compress', 'decompress', 'train_dict', 'finalize_dict',
           'ZstdCompressor', 'RichMemZstdCompressor', 'ZstdDecompressor',
           'ZstdDict', 'ZstdError', 'ZstdFile', 'zstd_open',
           'CParameter', 'DParameter', 'Strategy',
           'get_frame_info', 'get_frame_size',
           'zstd_version', 'zstd_version_info', 'compressionLevel_values')

import enum
import io
import os
import _compression
from collections import namedtuple

from ._zstd import *
from . import _zstd


_nt_values = namedtuple('values', ['default', 'min', 'max'])
compressionLevel_values = _nt_values(_zstd._ZSTD_CLEVEL_DEFAULT,
                                     _zstd._ZSTD_minCLevel,
                                     _zstd._ZSTD_maxCLevel)


_nt_frame_info = namedtuple('frame_info', ['decompressed_size', 'dictionary_id'])

def get_frame_info(frame_buffer):
    """
    Get zstd frame infomation from a frame header.

    frame_buffer: Py_buffer
        A bytes-like object. It should starts from the beginning of a frame, and
        needs to include at least the frame header (6 to 18 bytes).

    Return a two-items namedtuple: (decompressed_size, dictionary_id). If
    decompressed size is unknown (generated by stream compression), it will be
    None. If no dictionary, dictionary_id will be 0.

    It's possible to add more items to the namedtuple in the future."""

    ret_tuple = _zstd._get_frame_info(frame_buffer)
    return _nt_frame_info(*ret_tuple)


class CParameter(enum.IntEnum):
    compressionLevel           = _zstd._ZSTD_c_compressionLevel
    windowLog                  = _zstd._ZSTD_c_windowLog
    hashLog                    = _zstd._ZSTD_c_hashLog
    chainLog                   = _zstd._ZSTD_c_chainLog
    searchLog                  = _zstd._ZSTD_c_searchLog
    minMatch                   = _zstd._ZSTD_c_minMatch
    targetLength               = _zstd._ZSTD_c_targetLength
    strategy                   = _zstd._ZSTD_c_strategy

    enableLongDistanceMatching = _zstd._ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = _zstd._ZSTD_c_ldmHashLog
    ldmMinMatch                = _zstd._ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = _zstd._ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = _zstd._ZSTD_c_ldmHashRateLog

    contentSizeFlag            = _zstd._ZSTD_c_contentSizeFlag
    checksumFlag               = _zstd._ZSTD_c_checksumFlag
    dictIDFlag                 = _zstd._ZSTD_c_dictIDFlag

    nbWorkers                  = _zstd._ZSTD_c_nbWorkers
    jobSize                    = _zstd._ZSTD_c_jobSize
    overlapLog                 = _zstd._ZSTD_c_overlapLog

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        return _zstd._get_cparam_bounds(self.value)


class DParameter(enum.IntEnum):
    windowLogMax = _zstd._ZSTD_d_windowLogMax

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        return _zstd._get_dparam_bounds(self.value)


class Strategy(enum.IntEnum):
    """Compression strategies, listed from fastest to strongest.

       Note : new strategies _might_ be added in the future, only the order
       (from fast to strong) is guaranteed.
    """
    fast     = _zstd._ZSTD_fast
    dfast    = _zstd._ZSTD_dfast
    greedy   = _zstd._ZSTD_greedy
    lazy     = _zstd._ZSTD_lazy
    lazy2    = _zstd._ZSTD_lazy2
    btlazy2  = _zstd._ZSTD_btlazy2
    btopt    = _zstd._ZSTD_btopt
    btultra  = _zstd._ZSTD_btultra
    btultra2 = _zstd._ZSTD_btultra2


def compress(data, level_or_option=None, zstd_dict=None, rich_mem=False):
    """Compress a block of data.

    Refer to ZstdCompressor's docstring for a description of the optional
    arguments *level_or_option*, *zstd_dict*. Set *rich_mem* to True to enable
    rich memory mode.

    For incremental compression, use an ZstdCompressor instead.
    """
    if rich_mem:
        comp = RichMemZstdCompressor(level_or_option, zstd_dict)
        return comp.compress(data)
    else:
        comp = ZstdCompressor(level_or_option, zstd_dict)
        return comp.compress(data, ZstdCompressor.FLUSH_FRAME)


def decompress(data, zstd_dict=None, option=None):
    """Decompress a block of data.

    Refer to ZstdDecompressor's docstring for a description of the
    optional arguments *zstd_dict* and *option*.

    For incremental decompression, use an ZstdDecompressor instead.
    """
    decomp = ZstdDecompressor(zstd_dict, option)
    ret = decomp.decompress(data)

    if not decomp.at_frame_edge:
        raise ZstdError("Zstd data ends in an incomplete frame.")

    return ret


def train_dict(samples, dict_size):
    """Train a zstd dictionary, return a ZstdDict object.

    Arguments
    samples:   An iterable of samples, a sample is a bytes-like object
               represents a file.
    dict_size: The dictionary's maximum size, in bytes.
    """
    chunks = []
    chunk_sizes = []

    for chunk in samples:
        chunks.append(chunk)
        chunk_sizes.append(len(chunk))

    if not chunks:
        raise ValueError("The chunks is empty content, can't train dictionary.")

    chunks = b''.join(chunks)

    # chunks: samples be stored concatenated in a single flat buffer.
    # chunk_sizes: a list of each sample's size.
    # dict_size: size of the dictionary, in bytes.
    dict_content = _zstd._train_dict(chunks, chunk_sizes, dict_size)

    return ZstdDict(dict_content)


def finalize_dict(zstd_dict, samples, dict_size, level):
    """Finalize a zstd dictionary, return a ZstdDict object.

    This is an advanced function, see zstd documentation for usage.

    Only available when the underlying zstd library's version is
    greater than or equal to v1.4.5

    Arguments
    zstd_dict: An existing ZstdDict object.
    samples:   An iterable of samples, a sample is a bytes-like object
               represents a file.
    dict_size: The dictionary's maximum size, in bytes.
    level:     The compression level expected to use in production.
    """
    if zstd_version_info < (1, 4, 5):
        msg = ("This function only available when the underlying zstd "
               "library's version is greater than or equal to v1.4.5, "
               "the current underlying zstd library's version is v%s.") % zstd_version
        raise NotImplementedError(msg)

    if not isinstance(zstd_dict, ZstdDict):
        raise TypeError('zstd_dict argument should be a ZstdDict object.')

    chunks = []
    chunk_sizes = []

    for chunk in samples:
        chunks.append(chunk)
        chunk_sizes.append(len(chunk))

    if not chunks:
        raise ValueError("The chunks is empty content, can't train dictionary.")

    chunks = b''.join(chunks)

    # zstd_dict: existing dictionary.
    # chunks: samples be stored concatenated in a single flat buffer.
    # chunk_sizes: a list of each sample's size.
    # dict_size: maximal size of the dictionary, in bytes.
    # level: compression level expected to use in production.
    dict_content = _zstd._finalize_dict(zstd_dict.dict_content,
                                        chunks, chunk_sizes,
                                        dict_size, level)

    return ZstdDict(dict_content)


class EndlessDecompressReader(_compression.DecompressReader):
    """ Endless decompress reader for zstd, since zstd doesn't have
        an eof marker, the stream can be endless.
        End when underlying self._fp ends. """

    def read(self, size=-1):
        if size < 0:
            return self.readall()

        if not size or self._eof:
            return b""

        # Depending on the input data, our call to the decompressor may not
        # return any data. In this case, try again after reading another block.
        data = None
        while True:
            if self._decompressor.needs_input:
                in_dat = self._fp.read(_compression.BUFFER_SIZE)
                if not in_dat:
                    break
            else:
                in_dat = b""

            data = self._decompressor.decompress(in_dat, size)
            if data:
                break

        # self._fp ends
        if not data:
            if not self._decompressor.at_frame_edge:
                raise ZstdError("Zstd data ends in an incomplete frame.")

            self._eof = True
            self._size = self._pos  # decompressed size
            return b""

        # self._pos is current offset in decompressed stream
        self._pos += len(data)
        return data


_MODE_CLOSED   = 0
_MODE_READ     = 1
_MODE_WRITE    = 2

class ZstdFile(_compression.BaseStream):

    def __init__(self, filename=None, mode="r", *,
                 level_or_option=None, zstd_dict=None):
        self._fp = None
        self._closefp = False
        self._mode = _MODE_CLOSED

        if not isinstance(zstd_dict, (type(None), ZstdDict)):
            raise ValueError("zstd_dict should be ZstdDict object.")

        if mode in ("r", "rb"):
            if not isinstance(level_or_option, (type(None), dict)):
                raise ValueError("level_or_option should be dict object.")
            mode_code = _MODE_READ
        elif mode in ("w", "wb", "a", "ab", "x", "xb"):
            if not isinstance(level_or_option, (type(None), int, dict)):
                raise ValueError("level_or_option should be int or dict object.")
            mode_code = _MODE_WRITE
            self._compressor = ZstdCompressor(level_or_option, zstd_dict)
            self._pos = 0
        else:
            raise ValueError("Invalid mode: {!r}".format(mode))

        if isinstance(filename, (str, bytes, os.PathLike)):
            if "b" not in mode:
                mode += "b"
            self._fp = builtins.open(filename, mode)
            self._closefp = True
            self._mode = mode_code
        elif hasattr(filename, "read") or hasattr(filename, "write"):
            self._fp = filename
            self._mode = mode_code
        else:
            raise TypeError("filename must be a str, bytes, file or PathLike object")

        if self._mode == _MODE_READ:
            raw = EndlessDecompressReader(self._fp, ZstdDecompressor,
                trailing_error=ZstdError, zstd_dict=zstd_dict, option=level_or_option)
            self._buffer = io.BufferedReader(raw)

    def close(self):
        """Flush and close the file.

        May be called more than once without error. Once the file is
        closed, any other operation on it will raise a ValueError.
        """
        if self._mode == _MODE_CLOSED:
            return
        try:
            if self._mode == _MODE_READ:
                self._buffer.close()
                self._buffer = None
            elif self._mode == _MODE_WRITE:
                self._fp.write(self._compressor.flush())
                self._compressor = None
        finally:
            try:
                if self._closefp:
                    self._fp.close()
            finally:
                self._fp = None
                self._closefp = False
                self._mode = _MODE_CLOSED

    @property
    def closed(self):
        """True if this file is closed."""
        return self._mode == _MODE_CLOSED

    def fileno(self):
        """Return the file descriptor for the underlying file."""
        self._check_not_closed()
        return self._fp.fileno()

    def seekable(self):
        """Return whether the file supports seeking."""
        return self.readable() and self._buffer.seekable()

    def readable(self):
        """Return whether the file was opened for reading."""
        self._check_not_closed()
        return self._mode == _MODE_READ

    def writable(self):
        """Return whether the file was opened for writing."""
        self._check_not_closed()
        return self._mode == _MODE_WRITE

    def peek(self, size=-1):
        """Return buffered data without advancing the file position.

        Always returns at least one byte of data, unless at EOF.
        The exact number of bytes returned is unspecified.
        """
        self._check_can_read()
        # Relies on the undocumented fact that BufferedReader.peek() always
        # returns at least one byte (except at EOF)
        return self._buffer.peek(size)

    def read(self, size=-1):
        """Read up to size uncompressed bytes from the file.

        If size is negative or omitted, read until EOF is reached.
        Returns b"" if the file is already at EOF.
        """
        self._check_can_read()
        return self._buffer.read(size)

    def read1(self, size=-1):
        """Read up to size uncompressed bytes, while trying to avoid
        making multiple reads from the underlying stream. Reads up to a
        buffer's worth of data if size is negative.

        Returns b"" if the file is at EOF.
        """
        self._check_can_read()
        if size < 0:
            size = _compression.BUFFER_SIZE
        return self._buffer.read1(size)

    def readline(self, size=-1):
        """Read a line of uncompressed bytes from the file.

        The terminating newline (if present) is retained. If size is
        non-negative, no more than size bytes will be read (in which
        case the line may be incomplete). Returns b'' if already at EOF.
        """
        self._check_can_read()
        return self._buffer.readline(size)

    def write(self, data):
        """Write a bytes object to the file.

        Returns the number of uncompressed bytes written, which is
        always len(data). Note that due to buffering, the file on disk
        may not reflect the data written until close() is called.
        """
        self._check_can_write()
        compressed = self._compressor.compress(data)
        self._fp.write(compressed)
        self._pos += len(data)
        return len(data)

    def seek(self, offset, whence=io.SEEK_SET):
        """Change the file position.

        The new position is specified by offset, relative to the
        position indicated by whence. Possible values for whence are:

            0: start of stream (default): offset must not be negative
            1: current stream position
            2: end of stream; offset must not be positive

        Returns the new file position.

        Note that seeking is emulated, so depending on the parameters,
        this operation may be extremely slow.
        """
        self._check_can_seek()
        return self._buffer.seek(offset, whence)

    def tell(self):
        """Return the current file position."""
        self._check_not_closed()
        if self._mode == _MODE_READ:
            return self._buffer.tell()
        return self._pos


def zstd_open(filename, mode="rb", *, level_or_option=None, zstd_dict=None,
         encoding=None, errors=None, newline=None):
    if "t" in mode and "b" in mode:
        raise ValueError("Invalid mode: %r" % (mode,))

    zstd_mode = mode.replace("t", "")
    binary_file = ZstdFile(filename, zstd_mode,
                           level_or_option=level_or_option, zstd_dict=zstd_dict)

    if "t" in mode:
        return io.TextIOWrapper(binary_file, encoding, errors, newline)
    else:
        return binary_file
