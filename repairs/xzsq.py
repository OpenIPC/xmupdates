"""mksquashfs-compatible xz blocks.

mksquashfs compresses each block with liblzma's lzma_stream_buffer_encode(),
which records compressed and uncompressed sizes in the xz block header.
Python's lzma module cannot produce that, so this calls liblzma directly.
With LZMA2 preset 6, a 64 KiB dictionary and a CRC32 check it reproduces
XM's squashfs blocks bit for bit.
"""
import ctypes, ctypes.util
L=ctypes.CDLL(ctypes.util.find_library('lzma'))
LZMA_FILTER_LZMA2=0x21; LZMA_VLI_UNKNOWN=ctypes.c_uint64(-1).value; LZMA_CHECK_CRC32=1
class Filter(ctypes.Structure): _fields_=[('id',ctypes.c_uint64),('options',ctypes.c_void_p)]
L.lzma_lzma_preset.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
L.lzma_stream_buffer_encode.argtypes=[ctypes.POINTER(Filter),ctypes.c_int,ctypes.c_void_p,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_char_p,ctypes.POINTER(ctypes.c_size_t),ctypes.c_size_t]
def sq_xz(data, preset, dict_size=65536):
    opts=ctypes.create_string_buffer(256)
    if L.lzma_lzma_preset(opts, preset):
        raise ValueError(f'liblzma rejected preset {preset}')
    ctypes.c_uint32.from_buffer(opts,0).value=dict_size      # dict_size is the first field
    flt=(Filter*2)(Filter(LZMA_FILTER_LZMA2,ctypes.cast(opts,ctypes.c_void_p)),Filter(LZMA_VLI_UNKNOWN,None))
    out=ctypes.create_string_buffer(len(data)*2+1024); pos=ctypes.c_size_t(0)
    r=L.lzma_stream_buffer_encode(flt,LZMA_CHECK_CRC32,None,data,len(data),out,ctypes.byref(pos),len(out))
    if r != 0:
        raise RuntimeError(f'lzma_stream_buffer_encode failed: {r}')
    return out.raw[:pos.value]
