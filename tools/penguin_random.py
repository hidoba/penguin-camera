"""Timing-mixed image selection. Not a cryptographic RNG; no dither state.

Exact dumped firmware: 0x2144c returns the RAM uptime word below. The
periodic routine at 0x213dc adds 10, and 0x2146c uses it for delays.
Read ordinary RAM only: no peripheral reads, timer setup or clock writes.
"""
import struct

TICK_ADDRESS=0x0208671c
GETTER_OFFSET=0x2144c
GETTER=struct.pack('<8I',0x18600208,0xd7e10ffc,0xa863671c,0x9c21fffc,
                   0x85630000,0x9c210004,0x8421fffc,0x44004800)
UPDATE_OFFSET=0x21414
UPDATE=struct.pack('<5I',0x18800208,0xa884671c,0x84640000,0x9c63000a,0xd4041800)
MASK=0xffffffff


def validate_clock(original):
    for off,code in ((GETTER_OFFSET,GETTER),(UPDATE_OFFSET,UPDATE)):
        if original[off:off+len(code)]!=code:
            raise ValueError('Unrecognized firmware uptime source')


def next_state(state,tick):
    """Independent host oracle, with explicit 32-bit wrapping."""
    x=((state+0x9e3779b9)&MASK)^(tick&MASK)
    x=((x^(x>>16))*0x7feb352d)&MASK
    x=((x^(x>>15))*0x846ca68b)&MASK
    return x^(x>>16)


MAX_COUNT=64  # update 38 (was 16); the mapping below works for any 16-bit count


def choose(state,tick,count,last):
    if not 1<=count<=MAX_COUNT:raise ValueError('Invalid image count')
    state=next_state(state,tick)
    # Exclude the previous image before mapping, instead of redirecting its
    # probability to its neighbor. Modulo bias is at most one in 2**32.
    exclude=count>1 and 0<=last<count
    index=state%(count-1 if exclude else count)
    if exclude and index>=last:index+=1
    return state,index


def selection(a,count):
    """Inline event code: r18 points to worker STATE, returns image index r3.

    Only saved scratch registers 12/13/14/15/16 are used. Constant runtime,
    no rejection loop or allocations, including a frozen/wrapped uptime word.
    """
    if not 1<=count<=MAX_COUNT:raise ValueError('Invalid image count')
    imm=a.immediate
    imm(0x21,12,18,32);a.const(13,0x9e3779b9);a.alu(12,12,13)
    a.const(13,TICK_ADDRESS);imm(0x21,13,13,0);a.alu(12,12,13,5)
    for shift,multiplier in ((16,0x7feb352d),(15,0x846ca68b)):
        imm(0x2e,13,12,0x40|shift);a.alu(12,12,13,5)
        a.const(13,multiplier);a.alu(12,12,13,0x306)
    imm(0x2e,13,12,0x50);a.alu(12,12,13,5);a.store(12,18,32)
    imm(0x21,14,18,16);imm(0x27,13,0,count)
    if count>1:
        imm(0x2f,3,14,count);a.branch(4,'rng_map')
        imm(0x27,13,0,count-1)
    a.label('rng_map')
    a.alu(15,12,13,0x30a);a.alu(15,15,13,0x306);a.alu(3,12,15,2)
    if count>1:
        imm(0x2f,3,14,count);a.branch(4,'rng_selected')
        a.compare(3,14,4);a.branch(4,'rng_selected')
        imm(0x27,3,3,1)
    a.label('rng_selected')
