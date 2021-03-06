#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (c) 2016 Alexander Maul
#
# Author(s):
#
#   Alexander Maul <alexander.maul@dwd.de>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
'''
Collection of functions handling bits and bytes.

Created on Oct 28, 2016

@author: amaul
'''

import datetime
import logging
import struct
from errors import BufrDecodeError

logger = logging.getLogger("trollbufr")

def str2num(octets):
    """Convert all characters from octets (high->low) to int"""
    v = 0
    i = len(octets) - 1
    for b in octets:
        v |= ord(b) << 8 * i
        i -= 1
    return v

def octets2num(data, offset, count):
    """
    Convert character slice of length count from data (high->low) to int.

    Returns offset+count, the character after the converted characters, and the integer value.

    RETURN: offset,value
    """
    v = 0
    i = count - 1
    for b in data[offset : offset + count]:
        v |= ord(b) << 8 * i
        i -= 1
    return offset + count, v

def get_rval(data, comp, subs_num, tab_b_elem=None, alter=None, fix_width=None):
    """
    Read a raw value integer from the data section.

    The number of bits are either fixed or determined from Tab.B and previous alteration operators.
    Compression is taken into account.

    RETURN: raw value integer
    """
    if fix_width is not None:
        loc_width = fix_width
        logger.debug("OCTETS       : w+a:_+_ fw:%d qual:_ bc:%d #%d->ord(%02X)" ,
                    fix_width, data.bc, data.p, ord(data[data.p])
                    )
    elif tab_b_elem is not None and alter is not None:
        #
        # TODO: special handling for replication/repetition descriptor?
        #
#         if tab_b_elem.descr >= 10000 and (tab_b_elem.descr < 31000 or tab_b_elem.descr >= 31020):
#             pass
#         if tab_b_elem.descr >= 31000 or tab_b_elem.descr < 31020:
#             loc_width = tab_b_elem.width
        if tab_b_elem.typ == "string" and alter['wchr']:
            loc_width = alter['wchr']
        elif tab_b_elem.typ == "double" or tab_b_elem.typ == "long":
            if alter['ieee']:
                loc_width = alter['ieee']
            else:
                loc_width = tab_b_elem.width + alter['wnum']
        else:
            loc_width = tab_b_elem.width
        logger.debug("OCTETS %06d: w+a:%d%+d fw:_ qual:%d bc:%d #%d->ord(%02X)" ,
                    tab_b_elem.descr, tab_b_elem.width, alter['wnum'], alter['assoc'][-1],
                    data.bc, data.p, ord(data[data.p])
                    )
    else:
        raise BufrDecodeError("Can't determine width.")
    if comp:
        return cset2octets(data, loc_width, subs_num, tab_b_elem.typ if tab_b_elem is not None else "long")
    else:
        return data.get_bits(loc_width)

def cset2octets(data, loc_width, subs_num, btyp):
    """
    Like Blob.get_bits(), but for compressed data.
    RETURN: octets
    """
    min_val = data.get_bits(loc_width)
    cwidth = data.get_bits(6)
    if btyp == "string":
        cwidth *= 8
    if min_val == (1 << loc_width) - 1:
        # All missing
        v = (1 << loc_width) - 1
    elif cwidth == 0:
        # All equal
        v = min_val
    else:
        logger.debug("CSET loc_width %d  subnum %s  cwidth %d", loc_width, subs_num, cwidth)
        data.skip_bits(cwidth * subs_num[0])
        n = data.get_bits(cwidth)
        if n == (1 << cwidth) - 1:
            n = (1 << loc_width) - 1
        v = min_val + n
        data.skip_bits(cwidth * (subs_num[1] - subs_num[0] - 1))
    return v

def rval2str(rval):
    """Each byte of the integer rval is taken as a character, they are joined into a string"""
    octets = []
    while rval:
        if rval & 0xFF >= 0x20:
            octets.append(chr(rval & 0xFF))
        rval >>= 8
    octets.reverse()
    val = "".join(octets)
    return val

def rval2num(tab_b_elem, alter, rval):
    """
    Return the numeric value for all bits in rval decoded with descriptor descr,
    or type str if tab_b_elem describes a string.
    If the value was interpreted as "missing", None is returned.

    type(value):
    * numeric: int, float
    * codetable/flags: int
    * IA5 characters: string

    RETURN: value

    RAISE: KeyError if descr is not in table.
    """
    # Default return value is "missing value"
    val = None
    # The "missing-value" bit-masks for IEEE float/double
    _ieee32_miss = 0x7f7fffff
    _ieee64_miss = 0x7fefffffffffffff

    # Alter = {'wnum':0, 'wchr':0, 'refval':0, 'scale':0, 'assoc':0}
    if tab_b_elem.typ == "string" and alter['wchr']:
        loc_width = alter['wchr']
    else:
        loc_width = tab_b_elem.width + alter['wnum']
    loc_refval = alter['refval'].get(tab_b_elem.descr, tab_b_elem.refval * alter['refmul'])
    loc_scale = tab_b_elem.scale + alter['scale']

    logger.debug("EVAL %06d: typ:%s width:%d ref:%d scal:%d%+d",
                 tab_b_elem.descr, tab_b_elem.typ, loc_width, loc_refval, tab_b_elem.scale , alter['scale'])

    if rval == (1 << loc_width) - 1 and (tab_b_elem.descr < 31000 or tab_b_elem.descr >= 31020):
        # First, test if all bits are set, which usually means "missing value".
        # The delayed replication and repetition descr are special nut-cases.
        logger.debug("rval %d ==_(1<<%d)%d    #%06d/%d", rval, loc_width, (1 << loc_width) - 1, tab_b_elem.descr, tab_b_elem.descr / 1000)
        val = None
    elif alter['ieee'] and (tab_b_elem.typ == "double" or tab_b_elem.typ == "long"):
        # IEEE 32b or 64b floating point number, INF means missing.
        if alter['ieee'] != 32 and alter['ieee'] != 64:
            raise BufrDecodeError("Invalid IEEE size %d" % alter['ieee'])
        if alter['ieee'] == 32 and not rval ^ _ieee32_miss:
            val = struct.unpack("f", rval)
        elif alter['ieee'] == 64 and not rval ^ _ieee64_miss:
            val = struct.unpack("d", rval)
        else:
            val = None
    elif tab_b_elem.typ == "double" or loc_scale > 0:
        # Float/double: add reference, divide by scale
        val = float(rval + loc_refval) / 10 ** loc_scale
    elif tab_b_elem.typ == "long":
        # Integer: add reference, divide by scale
        val = (rval + loc_refval) / 10 ** loc_scale
    elif tab_b_elem.typ == "string":
        # For string, all bytes are reversed.
        val = rval2str(rval)
    else:
        val = rval

    logger.debug("DECODE %06d: (%d) -> \"%s\"" , tab_b_elem.descr, rval, str(val))

    return val

def b2s(n):
    """Builds a string with characters 0 and 1, representing the bits of an integer n."""
    a = 2 if n // 256 else 1
    m = 1 << 8 * a - 1
    return "".join([('1' if n & (m >> i) else '0') for i in range(0, 8 * a)])

def dtg(octets, ed=4):
    """
    Ed.3: year [yy], month, day, hour, minute
    Ed.4: year [yyyy], month, day, hour, minute, second
    """
    if ed == 3:
        o, yy = octets2num(octets, 0, 1)
    elif ed == 4:
        o, yy = octets2num(octets, 0, 2)
    o, mo = octets2num(octets, o, 1)
    o, dy = octets2num(octets, o, 1)
    o, hr = octets2num(octets, o, 1)
    o, mi = octets2num(octets, o, 1)
    if ed == 3:
        if yy > 50:
            yy += 1900
        else:
            yy += 2000
        sc = 0
    elif ed == 4:
        o, sc = octets2num(octets, o, 1)
    return datetime.datetime(yy, mo, dy, hr, mi, sc)

