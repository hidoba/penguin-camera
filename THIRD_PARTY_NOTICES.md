# Ditherista / libdither

The Magic 4×4 45 matrix (called Halftone 4×4 in this project), its threshold convention, and the Error Diffusion 1D
algorithm were adapted from Robert Kist's libdither, as used by Ditherista.

- Ditherista commit: `24f1067f1af359e1a52fd9298f56300c4d181b66`
- libdither commit: `7bf49c5324b8b8d5ce321d549f89fb6c75a9d859`
- Source: https://github.com/robertkist/libdither
- Files: `dither_ordered_data.h`, `dither_ordered.c`, `dither_errordiff_data.h`,
  `dither_errordiff.c`, `ditherimage.c`, `gamma.c`.

The camera implementation is an integer, zero-jitter adaptation. It treats
camera luminance as neutral sRGB; it does not reproduce the app's complete
color/adjustment pipeline. No kdtree or uthash code is used, but the notices
required by libdither's license are included below. Retain this file with
redistributed source and firmware binaries.

## libdither license (verbatim)

Copyright (C) 2022-2025 Robert Kist

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form and source code must include the kdtree
   copyright notice.

2. Redistributions in source code must include the uthash.h copyright notice.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR IMPLIED
WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO
EVENT SHALL THE AUTHOR BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT
OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY
OF SUCH DAMAGE.

## Required dependency copyright notices

kdtree: Copyright (C) 2007-2011 John Tsiombikas <nuclear@member.fsf.org>

uthash.h: Copyright (c) 2005-2025, Troy D. Hanson
https://troydhanson.github.io/uthash/
