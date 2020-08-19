
__all__ = ('compress', 'decompress', 'train_dict',
           'ZstdCompressor', 'ZstdDecompressor', 'ZstdDict', 'ZstdError',
           'ZstdFile', 'zstd_open',
           'CompressParameter', 'DecompressParameter',
           'Strategy', 'EndDirective',
           'get_frame_info', 'get_frame_size',
           'zstd_version', 'zstd_version_info', 'compress_level_bounds')

import enum
import io
import os
import _compression

from ._zstd import *
from . import _zstd


class CompressParameter(enum.IntEnum):
    compressionLevel           = ZSTD_c_compressionLevel
    windowLog                  = ZSTD_c_windowLog
    hashLog                    = ZSTD_c_hashLog
    chainLog                   = ZSTD_c_chainLog
    searchLog                  = ZSTD_c_searchLog
    minMatch                   = ZSTD_c_minMatch
    targetLength               = ZSTD_c_targetLength
    strategy                   = ZSTD_c_strategy
    enableLongDistanceMatching = ZSTD_c_enableLongDistanceMatching
    ldmHashLog                 = ZSTD_c_ldmHashLog
    ldmMinMatch                = ZSTD_c_ldmMinMatch
    ldmBucketSizeLog           = ZSTD_c_ldmBucketSizeLog
    ldmHashRateLog             = ZSTD_c_ldmHashRateLog
    contentSizeFlag            = ZSTD_c_contentSizeFlag
    checksumFlag               = ZSTD_c_checksumFlag
    dictIDFlag                 = ZSTD_c_dictIDFlag

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        return _zstd._get_cparam_bounds(self.value)
    

class DecompressParameter(enum.IntEnum):
    windowLogMax = ZSTD_d_windowLogMax

    def bounds(self):
        """Return lower and upper bounds of a parameter, both inclusive."""
        return _zstd._get_dparam_bounds(self.value)


class Strategy(enum.IntEnum):
    """Compression strategies, listed from fastest to strongest.

       Note : new strategies _might_ be added in the future, only the order
       (from fast to strong) is guaranteed.
    """
    fast     = ZSTD_fast
    dfast    = ZSTD_dfast
    greedy   = ZSTD_greedy
    lazy     = ZSTD_lazy
    lazy2    = ZSTD_lazy2
    btlazy2  = ZSTD_btlazy2
    btopt    = ZSTD_btopt
    btultra  = ZSTD_btultra
    btultra2 = ZSTD_btultra2


class EndDirective(enum.IntEnum):
    """Stream compressor's end directive.
    
    CONTINUE: Collect more data, encoder decides when to output compressed
              result, for optimal compression ratio. Usually used for ordinary
              streaming compression.
    FLUSH:    Flush any remaining data, but don't end current frame. Usually
              used for communication, the receiver can decode immediately.
    END:      Flush any remaining data _and_ close current frame.
    """
    CONTINUE = ZSTD_e_continue
    FLUSH    = ZSTD_e_flush
    END      = ZSTD_e_end


def compress(data, level_or_option=None, zstd_dict=None):
    """Compress a block of data.

    Refer to ZstdCompressor's docstring for a description of the
    optional arguments *level_or_option* and *zstd_dict*.

    For incremental compression, use an ZstdCompressor instead.
    """
    comp = ZstdCompressor(level_or_option, zstd_dict)
    return comp.compress(data, ZSTD_e_end)


def decompress(data, zstd_dict=None, option=None):
    """Decompress a block of data.

    Refer to ZstdDecompressor's docstring for a description of the
    optional arguments *zstd_dict* and *option*.

    For incremental decompression, use an ZstdDecompressor instead.
    """
    decomp = ZstdDecompressor(zstd_dict, option)
    return decomp.decompress(data)


def train_dict(iterable_of_chunks, dict_size=100*1024):
    """Train a zstd dictionary, return a ZstdDict object.

    In general:
    1) A reasonable dictionary has a size of ~100 KB. It's possible to select
       smaller or larger size, just by specifying dict_size argument.
    2) It's recommended to provide a few thousands samples, though this can
       vary a lot.
    3) It's recommended that total size of all samples be about ~x100 times the
       target size of dictionary.
    """
    chunks = []
    chunk_sizes = []

    for chunk in iterable_of_chunks:
        chunks.append(chunk)
        chunk_sizes.append(len(chunk))
    chunks = b''.join(chunks)

    # chunks: samples be stored concatenated in a single flat buffer.
    # chunk_sizes: a list of each sample's size.
    # dict_size: size of the dictionary, in bytes.
    dict_content = _zstd._train_dict(chunks, chunk_sizes, dict_size)

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